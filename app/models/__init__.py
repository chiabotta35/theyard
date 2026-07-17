from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_owner = db.Column(db.Boolean, default=False, nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    theme = db.Column(db.String(30), default="dark", nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    group_memberships = db.relationship(
        "GroupMembership", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    projects_created = db.relationship(
        "Project", backref="creator", lazy="dynamic", foreign_keys="Project.created_by"
    )
    assigned_tasks = db.relationship(
        "Task", backref="assignee", lazy="dynamic", foreign_keys="Task.assignee_id"
    )
    comments = db.relationship("Comment", backref="author", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_global_admin(self):
        return self.is_admin

    def has_project_permission(self, project_id, required_level="viewer"):
        if self.is_admin:
            return True

        perm = ProjectPermission.query.filter_by(
            project_id=project_id, user_id=self.id
        ).first()
        if perm and _level_rank(perm.permission_level) >= _level_rank(required_level):
            return True

        for membership in self.group_memberships:
            gperm = ProjectPermission.query.filter_by(
                project_id=project_id, group_id=membership.group_id
            ).first()
            if gperm and _level_rank(gperm.permission_level) >= _level_rank(
                required_level
            ):
                return True

        return False

    def get_highest_project_role(self, project_id):
        if self.is_admin:
            return "admin"

        best = None
        perm = ProjectPermission.query.filter_by(
            project_id=project_id, user_id=self.id
        ).first()
        if perm:
            best = perm.permission_level

        for membership in self.group_memberships:
            gperm = ProjectPermission.query.filter_by(
                project_id=project_id, group_id=membership.group_id
            ).first()
            if gperm:
                if best is None or _level_rank(gperm.permission_level) > _level_rank(
                    best
                ):
                    best = gperm.permission_level

        return best

    def can_manage_all_projects(self):
        return self.is_admin or self.is_owner


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.Text, default="")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    memberships = db.relationship(
        "GroupMembership", backref="group", lazy="dynamic", cascade="all, delete-orphan"
    )
    project_permissions = db.relationship(
        "ProjectPermission", backref="group", lazy="dynamic"
    )


class GroupMembership(db.Model):
    __tablename__ = "group_memberships"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="member")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", "group_id", name="uq_user_group"),
    )


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    status = db.Column(
        db.String(20), nullable=False, default="active"
    )
    prefix = db.Column(db.String(10), nullable=False, default="")
    color = db.Column(db.String(30), nullable=False, default="#00e676")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    permissions = db.relationship(
        "ProjectPermission", backref="project", lazy="dynamic", cascade="all, delete-orphan"
    )
    tasks = db.relationship("Task", backref="project", lazy="dynamic", cascade="all, delete-orphan")


class ProjectPermission(db.Model):
    __tablename__ = "project_permissions"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=True)
    permission_level = db.Column(db.String(20), nullable=False, default="viewer")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        db.CheckConstraint(
            "(user_id IS NOT NULL AND group_id IS NULL) OR (user_id IS NULL AND group_id IS NOT NULL)",
            name="ck_perm_target",
        ),
    )


TASK_STATUSES = ["todo", "in_progress", "review", "done"]
TASK_PRIORITIES = ["low", "medium", "high", "critical"]
PROJECT_STATUSES = ["draft", "active", "archived"]
LABEL_COLORS = [
    "var(--accent)", "var(--success)", "var(--warning)",
    "var(--danger)", "var(--orange)", "#a78bfa", "#f472b6", "#38bdf8",
]


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    task_number = db.Column(db.Integer, nullable=False, default=0)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    status = db.Column(db.String(20), nullable=False, default="todo")
    priority = db.Column(db.String(20), nullable=False, default="medium")
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    comments = db.relationship("Comment", backref="task", lazy="dynamic", cascade="all, delete-orphan")
    creator = db.relationship("User", foreign_keys=[created_by], backref="created_tasks")

    @property
    def display_id(self):
        proj = self.project
        prefix = proj.prefix.upper() if proj and proj.prefix else "T"
        return f"{prefix}-{self.task_number}"


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


LEVEL_RANKS = {"viewer": 0, "editor": 1, "admin": 2}

WEBHOOK_EVENTS = [
    "task.created",
    "task.updated",
    "task.status_changed",
    "task.deleted",
    "comment.created",
    "project.created",
    "project.updated",
    "project.deleted",
]


def _level_rank(level):
    return LEVEL_RANKS.get(level, -1)


class Webhook(db.Model):
    __tablename__ = "webhooks"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False)
    name = db.Column(db.String(80), nullable=False, default="")
    secret = db.Column(db.String(128), nullable=True)
    events = db.Column(db.Text, nullable=False, default="task.created,task.updated,task.status_changed,comment.created")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    creator = db.relationship("User", backref="created_webhooks")

    def get_events_list(self):
        return [e.strip() for e in self.events.split(",") if e.strip()]

    def set_events_list(self, event_list):
        self.events = ",".join(event_list)


class Label(db.Model):
    __tablename__ = "labels"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.String(40), nullable=False)
    color = db.Column(db.String(30), nullable=False, default="var(--accent)")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    project = db.relationship("Project", backref="labels")


class TaskLabel(db.Model):
    __tablename__ = "task_labels"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    label_id = db.Column(db.Integer, db.ForeignKey("labels.id"), nullable=False)

    task = db.relationship("Task", backref="task_labels")
    label = db.relationship("Label", backref="label_tasks")

    __table_args__ = (
        db.UniqueConstraint("task_id", "label_id", name="uq_task_label"),
    )


class ActivityLog(db.Model):
    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(20), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    entity_name = db.Column(db.String(200), nullable=False, default="")
    detail = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    project = db.relationship("Project", backref="activities")
    user = db.relationship("User", backref="activities")


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    mime_type = db.Column(db.String(100), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    task = db.relationship("Task", backref="attachments")
    user = db.relationship("User", backref="uploads")


class TaskDependency(db.Model):
    __tablename__ = "task_dependencies"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    blocked_by_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)

    task = db.relationship("Task", foreign_keys=[task_id], backref="blocking")
    blocked_by = db.relationship("Task", foreign_keys=[blocked_by_id], backref="blocks")

    __table_args__ = (
        db.UniqueConstraint("task_id", "blocked_by_id", name="uq_task_dep"),
        db.CheckConstraint("task_id != blocked_by_id", name="ck_no_self_dep"),
    )


class RecurringTask(db.Model):
    __tablename__ = "recurring_tasks"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    priority = db.Column(db.String(20), nullable=False, default="medium")
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    interval_days = db.Column(db.Integer, nullable=False, default=7)
    last_run = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    project = db.relationship("Project", backref="recurring_tasks")
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    creator = db.relationship("User", foreign_keys=[created_by])


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(300), nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    user = db.relationship("User", backref="notifications")


def log_activity(project_id, user_id, action, entity_type, entity_id, entity_name, detail=None):
    entry = ActivityLog(
        project_id=project_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        detail=detail,
    )
    db.session.add(entry)


def notify(user_id, title, body=None, url=None):
    entry = Notification(
        user_id=user_id,
        title=title,
        body=body,
        url=url,
    )
    db.session.add(entry)
