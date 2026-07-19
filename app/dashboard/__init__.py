from datetime import date, timedelta, datetime

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

    total_tasks = len(my_tasks)
    done_this_week = Task.query.filter(
        Task.assignee_id == current_user.id,
        Task.status == "done",
        Task.updated_at >= datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time()),
    ).count()

    active_projects = len(project_stats)
    completed_count = sum(ps["stats"].get("done", 0) for ps in project_stats)
    total_count = sum(ps["stats"].get("total", 0) for ps in project_stats)

    stale_days = 14
    stale_projects = []
    if project_ids:
        projects_list = Project.query.filter(Project.id.in_(project_ids)).all()
        for proj in projects_list:
            age = (today - proj.updated_at.date()).days if proj.updated_at else 0
            if age >= stale_days:
                stale_projects.append({"project": proj, "days": age})

    overdue_all = (
        Task.query
        .filter(
            Task.project_id.in_(project_ids),
            Task.due_date < today,
            Task.status != "done",
        )
        .count()
    )

    due_today = (
        Task.query
        .filter(
            Task.project_id.in_(project_ids),
            Task.due_date == today,
            Task.status != "done",
        )
        .count()
    )

    due_tomorrow = (
        Task.query
        .filter(
            Task.project_id.in_(project_ids),
            Task.due_date == today + timedelta(days=1),
            Task.status != "done",
        )
        .count()
    )

    heatmap_end = today
    heatmap_start = today - timedelta(days=83)
    activity_counts = {}
    if project_ids:
        logs = (
            ActivityLog.query
            .filter(
                ActivityLog.project_id.in_(project_ids),
                ActivityLog.action.in_(["completed", "created"]),
                ActivityLog.created_at >= datetime.combine(heatmap_start, datetime.min.time()),
            )
            .all()
        )
        for log in logs:
            day = log.created_at.date() if log.created_at else None
            if day:
                activity_counts[day] = activity_counts.get(day, 0) + 1

    heatmap_data = []
    current = heatmap_start
    while current <= heatmap_end:
        count = activity_counts.get(current, 0)
        heatmap_data.append({"date": current.isoformat(), "count": count, "weekday": current.weekday()})
        current += timedelta(days=1)

    week_start = today - timedelta(days=today.weekday())
    week_days = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_tasks = []
        for t in my_tasks:
            if t.due_date == day:
                day_tasks.append({"title": t.title, "id": t.id, "priority": t.priority})
        week_days.append({
            "date": day,
            "label": day.strftime("%a"),
            "num": day.strftime("%d"),
            "is_today": day == today,
            "tasks": day_tasks,
        })

    digest = []
    if due_today > 0:
        digest.append({"color": "red", "text": f"{due_today} task{'s' if due_today != 1 else ''} due today"})
    if overdue_all > 0:
        digest.append({"color": "red", "text": f"{overdue_all} overdue task{'s' if overdue_all != 1 else ''} across all projects"})
    if due_tomorrow > 0:
        digest.append({"color": "yellow", "text": f"{due_tomorrow} due tomorrow"})
    if done_this_week > 0:
        digest.append({"color": "green", "text": f"You completed {done_this_week} task{'s' if done_this_week != 1 else ''} this week"})
    if stale_projects:
        oldest = max(stale_projects, key=lambda x: x["days"])
        digest.append({"color": "blue", "text": f"'{oldest['project'].name}' hasn't been updated in {oldest['days']} days"})
    if not digest:
        digest.append({"color": "green", "text": "All clear — no pressing items"})

    return render_template(
        "dashboard/index.html",
        my_tasks=my_tasks,
        overdue=overdue,
        recently_completed=recently_completed,
        recent_activity=recent_activity,
        project_stats=project_stats,
        unread_notifications=unread_notifications,
        total_tasks=total_tasks,
        done_this_week=done_this_week,
        active_projects=active_projects,
        completed_count=completed_count,
        total_count=total_count,
        digest=digest,
        heatmap_data=heatmap_data,
        week_days=week_days,
    )


@dashboard_bp.route("/notifications/mark-read", methods=["POST"])
@login_required
def mark_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(url_for("dashboard.index"))
