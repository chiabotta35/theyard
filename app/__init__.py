import os
import logging
from pathlib import Path
from flask import Flask, redirect, url_for
from flask_login import LoginManager, current_user
from datetime import date
from flask_migrate import Migrate

from .config import Config
from .models import db, User

logger = logging.getLogger(__name__)

_version_file = Path(__file__).resolve().parent.parent.joinpath("VERSION")
_raw_version = os.environ.get("THEYARD_VERSION") or (
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
            db.session.commit()
            logger.info("Admin user '%s' created.", admin_username)
        else:
            logger.info("Admin user '%s' already exists, skipping.", admin_username)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(kanban_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(recurring_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(gantt_bp)

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    @app.context_processor
    def inject_globals():
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

    return app
