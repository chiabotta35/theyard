from datetime import date, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField
from wtforms.validators import DataRequired

from ..models import (
    db, Task, RecurringTask, User, Project, ProjectPermission, GroupMembership,
    log_activity, notify,
)
from ..webhooks import fire_webhook, build_task_payload

recurring_bp = Blueprint("recurring", __name__, url_prefix="/recurring")

RECURRING_PRIORITIES = ["low", "medium", "high", "critical"]
INTERVAL_CHOICES = [("1", "Every day"), ("3", "Every 3 days"), ("7", "Every week"),
                    ("14", "Every 2 weeks"), ("30", "Every month"), ("90", "Every quarter")]


class RecurringTaskForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired()])
    description = TextAreaField("Description")
    priority = SelectField("Priority", choices=[(p, p.title()) for p in RECURRING_PRIORITIES])
    assignee_id = SelectField("Assignee", coerce=lambda x: int(x) if x and str(x).strip() else None)
    interval_days = SelectField("Repeat interval", choices=INTERVAL_CHOICES)


def _assignee_choices(project_id):
    user_ids = set()
    for perm in ProjectPermission.query.filter_by(project_id=project_id).all():
        if perm.user_id:
            user_ids.add(perm.user_id)
        if perm.group_id:
            for m in GroupMembership.query.filter_by(group_id=perm.group_id).all():
                user_ids.add(m.user_id)
    if not user_ids:
        user_ids = {u.id for u in User.query.filter_by(is_active_user=True).all()}
    users = User.query.filter(User.id.in_(user_ids)).order_by(User.username).all()
    return [(None, "-- Unassigned --")] + [(u.id, u.username) for u in users]


def _next_task_number(project_id):
    max_num = db.session.query(db.func.max(Task.task_number)).filter_by(project_id=project_id).scalar()
    return (max_num or 0) + 1


@recurring_bp.route("/")
@login_required
def list_recurring_all():
    project_ids = []
    for p in Project.query.all():
        if current_user.has_project_permission(p.id, "viewer"):
            project_ids.append(p.id)
    tasks = (
        RecurringTask.query
        .filter(RecurringTask.project_id.in_(project_ids))
        .order_by(RecurringTask.created_at.desc())
        .all()
    )
    return render_template("recurring/all.html", tasks=tasks)


@recurring_bp.route("/project/<int:project_id>")
@login_required
def list_recurring(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id, "viewer"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    tasks = RecurringTask.query.filter_by(project_id=project_id).order_by(RecurringTask.created_at.desc()).all()
    form = RecurringTaskForm()
    form.assignee_id.choices = _assignee_choices(project_id)
    return render_template("recurring/list.html", project=project, tasks=tasks, form=form)


@recurring_bp.route("/project/<int:project_id>/create", methods=["POST"])
@login_required
def create_recurring(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id, "editor"):
        flash("Access denied.", "danger")
        return redirect(url_for("recurring.list_recurring", project_id=project_id))
    form = RecurringTaskForm()
    form.assignee_id.choices = _assignee_choices(project_id)
    if form.validate_on_submit():
        rt = RecurringTask(
            project_id=project_id,
            title=form.title.data,
            description=form.description.data or "",
            priority=form.priority.data,
            assignee_id=form.assignee_id.data,
            created_by=current_user.id,
            interval_days=int(form.interval_days.data),
        )
        db.session.add(rt)
        log_activity(project_id, current_user.id, "created", "recurring_task", None, form.title.data)
        db.session.commit()
        flash(f"Recurring task '{rt.title}' created.", "success")
    else:
        flash("Invalid form data.", "danger")
    return redirect(url_for("recurring.list_recurring", project_id=project_id))


@recurring_bp.route("/<int:recurring_id>/delete", methods=["POST"])
@login_required
def delete_recurring(recurring_id):
    rt = db.session.get(RecurringTask, recurring_id)
    if not rt:
        flash("Recurring task not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(rt.project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("recurring.list_recurring", project_id=rt.project_id))
    project_id = rt.project_id
    title = rt.title
    db.session.delete(rt)
    log_activity(project_id, current_user.id, "deleted", "recurring_task", recurring_id, title)
    db.session.commit()
    flash("Recurring task deleted.", "success")
    return redirect(url_for("recurring.list_recurring", project_id=project_id))


@recurring_bp.route("/<int:recurring_id>/toggle", methods=["POST"])
@login_required
def toggle_recurring(recurring_id):
    rt = db.session.get(RecurringTask, recurring_id)
    if not rt:
        flash("Recurring task not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(rt.project_id, "editor"):
        flash("Access denied.", "danger")
        return redirect(url_for("recurring.list_recurring", project_id=rt.project_id))
    rt.is_active = not rt.is_active
    action = "activated" if rt.is_active else "deactivated"
    log_activity(rt.project_id, current_user.id, action, "recurring_task", rt.id, rt.title)
    db.session.commit()
    flash(f"Recurring task {action}.", "success")
    return redirect(url_for("recurring.list_recurring", project_id=rt.project_id))


@recurring_bp.route("/project/<int:project_id>/check", methods=["POST"])
@login_required
def check_recurring(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    today = date.today()
    tasks = RecurringTask.query.filter_by(project_id=project_id, is_active=True).all()
    created_count = 0
    for rt in tasks:
        if rt.last_run is None or rt.last_run + timedelta(days=rt.interval_days) <= today:
            task = Task(
                project_id=project_id,
                task_number=_next_task_number(project_id),
                title=rt.title,
                description=rt.description,
                status="todo",
                priority=rt.priority,
                assignee_id=rt.assignee_id,
                created_by=rt.created_by,
            )
            db.session.add(task)
            db.session.flush()
            rt.last_run = today
            log_activity(project_id, rt.created_by, "created", "task", task.id, task.title, detail="from recurring schedule")
            fire_webhook("task.created", build_task_payload(task, "created"))
            if task.assignee_id and task.assignee_id != rt.created_by:
                notify(task.assignee_id, f"Recurring task created: {task.display_id} — {task.title}", url=f"/tasks/{task.id}")
            created_count += 1
    db.session.commit()
    flash(f"Created {created_count} task(s) from recurring schedule.", "success")
    return redirect(url_for("recurring.list_recurring", project_id=project_id))
