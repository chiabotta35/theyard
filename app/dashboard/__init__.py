from datetime import date, timedelta
from collections import Counter

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

    # Aggregate stats for charts
    all_my_tasks = Task.query.filter(Task.assignee_id == current_user.id).all()
    status_counts = Counter(t.status for t in all_my_tasks)
    priority_counts = Counter(t.priority for t in all_my_tasks if t.status != "done")

    # Activity last 7 days
    week_ago = date.today() - timedelta(days=6)
    activity_days = []
    for i in range(7):
        d = week_ago + timedelta(days=i)
        day_label = d.strftime("%a")
        count = ActivityLog.query.filter(
            ActivityLog.project_id.in_(project_ids),
            db.func.date(ActivityLog.created_at) == d,
        ).count()
        activity_days.append({"label": day_label, "count": count})
    max_activity = max((d["count"] for d in activity_days), default=1) or 1

    return render_template(
        "dashboard/index.html",
        my_tasks=my_tasks,
        overdue=overdue,
        recently_completed=recently_completed,
        recent_activity=recent_activity,
        project_stats=project_stats,
        unread_notifications=unread_notifications,
        status_counts=status_counts,
        priority_counts=priority_counts,
        activity_days=activity_days,
        max_activity=max_activity,
        total_my_tasks=len(all_my_tasks),
    )


@dashboard_bp.route("/notifications/mark-read", methods=["POST"])
@login_required
def mark_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("dashboard.index"))
