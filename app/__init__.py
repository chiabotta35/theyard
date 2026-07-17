import os
import logging
from pathlib import Path
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate

from .config import Config
from .models import db, User

logger = logging.getLogger(__name__)

_version_file = Path(__file__).resolve().parent.parent.joinpath("VERSION")
VERSION = os.environ.get("THEYARD_VERSION") or (
    _version_file.read_text().strip() if _version_file.exists() else "dev"
)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


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

    app.register_blueprint(auth_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(kanban_bp)
    app.register_blueprint(webhooks_bp)

    @app.context_processor
    def inject_globals():
        from .models import TASK_STATUSES, TASK_PRIORITIES, PROJECT_STATUSES

        return {
            "task_statuses": TASK_STATUSES,
            "task_priorities": TASK_PRIORITIES,
            "project_statuses": PROJECT_STATUSES,
            "app_version": VERSION,
        }

    with app.app_context():
        data_dir = Path(app.instance_path).parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db.create_all()
        _seed_admin(app)

    return app
