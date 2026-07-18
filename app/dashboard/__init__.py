from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request
from flask_login import login_required, current_user

from ..models import (
    db, Task, User, Project, ProjectPermission, ActivityLog, Notification,
)

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _visible_project_ids(user):
    if user.can_manage_all_projects():
        return [p.id for p in Project.query.all()]
    ids = set()
    for perm in ProjectPermission.query.filter(ProjectPermission.user_id == user.id).all():
        ids.add(perm.project_id)
    for m in user.group_memberships:
        for perm in ProjectPermission.query.filter(ProjectPermission.group_id == m.group_id).all():
            ids.add(perm.project_id)
    return list(ids)


@dashboard_bp.route("/")
@login_required
def index():
    today = date.today()
    project_ids = _visible_project_ids(current_user)

    my_tasks = (
        Task.query
        .filter(Task.assignee_id == current_user.id, Task.status != "done")
        .order_by(Task.due_date.asc().nullslast(), Task.priority.desc())
        .all()
    )

    overdue = (
        Task.query
        .filter(
            Task.assignee_id == current_user.id,
            Task.due_date < today,
            Task.status != "done",
        )
        .order_by(Task.due_date.asc())
        .all()
    )

    recently_completed = (
        Task.query
        .filter(Task.assignee_id == current_user.id, Task.status == "done")
        .order_by(Task.updated_at.desc())
        .limit(5)
        .all()
    )

    recent_activity = (
        ActivityLog.query
        .filter(ActivityLog.project_id.in_(project_ids))
        .order_by(ActivityLog.created_at.desc())
        .limit(15)
        .all()
    )

    project_stats = []
    if project_ids:
        projects = Project.query.filter(Project.id.in_(project_ids)).order_by(Project.name).all()
        for proj in projects:
            tasks = proj.tasks.all()
            stats = {s: 0 for s in ["todo", "in_progress", "review", "done"]}
            for t in tasks:
                if t.status in stats:
                    stats[t.status] += 1
            stats["total"] = len(tasks)
            project_stats.append({"project": proj, "stats": stats})

    unread_notifications = (
        Notification.query
        .filter_by(user_id=current_user.id, is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "dashboard/index.html",
        my_tasks=my_tasks,
        overdue=overdue,
        recently_completed=recently_completed,
        recent_activity=recent_activity,
        project_stats=project_stats,
        unread_notifications=unread_notifications,
    )


@dashboard_bp.route("/notifications/mark-read", methods=["POST"])
@login_required
def mark_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("dashboard.index"))
