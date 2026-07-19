import os
import logging
from pathlib import Path
from flask import Flask, redirect, url_for
from flask_login import LoginManager, current_user
from datetime import date
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .models import db, User

logger = logging.getLogger(__name__)

_version_file = Path(__file__).resolve().parent.parent.joinpath("VERSION")
_raw_version = os.environ.get("TASKIT_VERSION") or (
    _version_file.read_text().strip() if _version_file.exists() else "dev"
)
VERSION = _raw_version.lstrip("v")

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def _ensure_columns(app):
    """Add missing columns to existing tables (SQLite doesn't support ALTER TABLE ADD COLUMN IF NOT EXISTS)."""
    with app.app_context():
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        additions = {
            "tasks": [
                ("due_date", "DATE"),
                ("task_number", "INTEGER DEFAULT 0"),
            ],
            "projects": [
                ("prefix", "VARCHAR(10) DEFAULT ''"),
                ("color", "VARCHAR(30) DEFAULT '#00e676'"),
            ],
            "users": [
                ("theme", "VARCHAR(30) DEFAULT 'dark'"),
            ],
        }
        for table, cols in additions.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_type in cols:
                if col_name not in existing:
                    try:
                        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                        db.session.commit()
                        logger.info("Added column %s.%s", table, col_name)
                    except Exception:
                        db.session.rollback()


def _seed_admin(app):
    with app.app_context():
        admin_username = app.config.get("ADMIN_USERNAME", "admin")
        admin_email = app.config.get("ADMIN_EMAIL", "admin@example.com")
        admin_password = app.config.get("ADMIN_PASSWORD", "changeme")

        existing = User.query.filter_by(username=admin_username).first()
        if not existing:
            admin = User(
                username=admin_username,
                email=admin_email,
                is_admin=True,
                is_owner=True,
                is_active_user=True,
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            try:
                db.session.commit()
                logger.info("Admin user '%s' created.", admin_username)
            except Exception:
                db.session.rollback()
                logger.info("Admin user '%s' already exists (race), skipping.", admin_username)
        else:
            logger.info("Admin user '%s' already exists, skipping.", admin_username)


def _check_aging_tasks(app):
    from datetime import date, timedelta, datetime, timezone
    from .models import db, Task, Project, ProjectAgingSetting, TaskAgingLog, Notification, User
    from .webhooks import fire_webhook

    settings = ProjectAgingSetting.query.filter_by(enabled=True).all()
    for setting in settings:
        threshold = setting.days_threshold
        cutoff = date.today() - timedelta(days=threshold)

        tasks = Task.query.filter(
            Task.project_id == setting.project_id,
            Task.status != "done",
            Task.created_at <= datetime.combine(cutoff, datetime.min.time()).replace(tzinfo=timezone.utc),
        ).all()

        for task in tasks:
            existing = TaskAgingLog.query.filter_by(task_id=task.id).first()
            if existing:
                days_stuck = (date.today() - existing.notified_at.date()).days if existing.notified_at else 0
                if days_stuck < threshold:
                    continue

            days_stuck = (date.today() - task.created_at.date()).days if task.created_at else 0
            log = TaskAgingLog(task_id=task.id, status_at_check=task.status, days_stuck=days_stuck)
            db.session.add(log)

            if setting.notify_assignee and task.assignee_id:
                notif = Notification(
                    user_id=task.assignee_id,
                    title=f"Task aging: {task.title}",
                    body=f"Has been in '{task.status}' for {days_stuck} days",
                    url=f"/tasks/{task.id}",
                )
                db.session.add(notif)

            if setting.notify_owner and setting.project:
                owner_id = setting.project.created_by
                if owner_id and owner_id != task.assignee_id:
                    notif = Notification(
                        user_id=owner_id,
                        title=f"Aging task: {task.title}",
                        body=f"In project '{setting.project.name}' for {days_stuck} days",
                        url=f"/tasks/{task.id}",
                    )
                    db.session.add(notif)

            if setting.notify_webhook and setting.webhook_url:
                try:
                    import requests as req
                    payload = {
                        "event": "task.aging",
                        "task_id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "days_stuck": days_stuck,
                        "project": setting.project.name if setting.project else "",
                    }
                    req.post(setting.webhook_url, json=payload, timeout=10)
                except Exception:
                    pass

        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    db.init_app(app)
    login_manager.init_app(app)
    Migrate(app, db)

    from .auth import auth_bp
    from .projects import projects_bp
    from .tasks import tasks_bp
    from .kanban import kanban_bp
    from .webhooks_ui import webhooks_bp
    from .search import search_bp
    from .recurring import recurring_bp
    from .dashboard import dashboard_bp
    from .gantt import gantt_bp
    from .mytasks import mytasks_bp
    from .global_gantt import global_gantt_bp
    from .reports import reports_bp
    from .templates_mod import templates_bp
    from .api import api_bp
    from .timetrack import timetrack_bp
    from .assets import assets_bp
    from .ical_export import ical_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(kanban_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(recurring_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(gantt_bp)
    app.register_blueprint(mytasks_bp)
    app.register_blueprint(global_gantt_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(timetrack_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(ical_bp)

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    @app.context_processor
    def inject_globals():
        from flask import session as flask_session
        from flask_wtf.csrf import generate_csrf
        from .models import TASK_STATUSES, TASK_PRIORITIES, PROJECT_STATUSES, Notification

        sidebar_projects = []
        unread_count = 0
        notifications = []
        if current_user.is_authenticated:
            from .models import Project, ProjectPermission
            if current_user.can_manage_all_projects():
                sidebar_projects = Project.query.order_by(Project.updated_at.desc()).limit(20).all()
            else:
                project_ids = set()
                for perm in ProjectPermission.query.filter(ProjectPermission.user_id == current_user.id).all():
                    project_ids.add(perm.project_id)
                for m in current_user.group_memberships:
                    for perm in ProjectPermission.query.filter(ProjectPermission.group_id == m.group_id).all():
                        project_ids.add(perm.project_id)
                sidebar_projects = Project.query.filter(Project.id.in_(project_ids)).order_by(Project.updated_at.desc()).limit(20).all()
            unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(20).all()

        return {
            "task_statuses": TASK_STATUSES,
            "task_priorities": TASK_PRIORITIES,
            "project_statuses": PROJECT_STATUSES,
            "app_version": VERSION,
            "sidebar_projects": sidebar_projects,
            "now": date.today().isoformat(),
            "unread_count": unread_count,
            "notifications": notifications,
            "csrf_token": lambda: generate_csrf(),
            "csrf_key": flask_session.get("csrf_token", ""),
        }

    with app.app_context():
        data_dir = Path(app.instance_path).parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            db.create_all()
        except Exception:
            db.session.rollback()
        _ensure_columns(app)
        _seed_admin(app)
        _check_aging_tasks(app)

    return app
