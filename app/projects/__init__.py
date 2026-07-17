from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField
from wtforms.validators import DataRequired

from ..models import db, Project, ProjectPermission, User, Group, Label, TaskLabel, log_activity, LABEL_COLORS
from ..webhooks import fire_webhook, build_project_payload

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")


class ProjectForm(FlaskForm):
    name = StringField("Project Name", validators=[DataRequired()])
    description = TextAreaField("Description")
    status = SelectField("Status", choices=[(s, s.replace("_", " ").title()) for s in ["draft", "active", "archived"]])


class PermissionForm(FlaskForm):
    target_type = SelectField("Type", choices=[("user", "User"), ("group", "Group")])
    target_id = SelectField("Target", coerce=int)
    permission_level = SelectField(
        "Permission",
        choices=[("viewer", "Viewer"), ("editor", "Editor"), ("admin", "Admin")],
    )


def _get_user_pool():
    return User.query.filter_by(is_active_user=True).order_by(User.username).all()


def _get_group_pool():
    return Group.query.order_by(Group.name).all()


@projects_bp.route("/")
@login_required
def list_projects():
    if current_user.can_manage_all_projects():
        projects = Project.query.order_by(Project.updated_at.desc()).all()
    else:
        project_ids = set()
        for perm in ProjectPermission.query.filter(
            ProjectPermission.user_id == current_user.id
        ).all():
            project_ids.add(perm.project_id)
        for membership in current_user.group_memberships:
            for perm in ProjectPermission.query.filter(
                ProjectPermission.group_id == membership.group_id
            ).all():
                project_ids.add(perm.project_id)
        projects = Project.query.filter(Project.id.in_(project_ids)).order_by(
            Project.updated_at.desc()
        ).all()
    return render_template("projects/list.html", projects=projects)


@projects_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_project():
    form = ProjectForm()
    if form.validate_on_submit():
        project = Project(
            name=form.name.data,
            description=form.description.data,
            status=form.status.data,
            created_by=current_user.id,
        )
        db.session.add(project)
        db.session.flush()
        perm = ProjectPermission(
            project_id=project.id, user_id=current_user.id, permission_level="admin"
        )
        db.session.add(perm)
        db.session.commit()
        fire_webhook("project.created", build_project_payload(project, "created"))
        flash(f"Project '{project.name}' created.", "success")
        return redirect(url_for("projects.detail_project", project_id=project.id))
    return render_template("projects/form.html", form=form, title="Create Project")


@projects_bp.route("/<int:project_id>")
@login_required
def detail_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.can_manage_all_projects() and not current_user.has_project_permission(project_id):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    from ..models import Task
    tasks = Task.query.filter_by(project_id=project_id).order_by(Task.position, Task.created_at.desc()).all()
    role = current_user.get_highest_project_role(project_id)
    return render_template(
        "projects/detail.html", project=project, tasks=tasks, role=role
    )


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.detail_project", project_id=project_id))
    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        project.name = form.name.data
        project.description = form.description.data
        project.status = form.status.data
        db.session.commit()
        fire_webhook("project.updated", build_project_payload(project, "updated"))
        flash(f"Project '{project.name}' updated.", "success")
        return redirect(url_for("projects.detail_project", project_id=project_id))
    return render_template("projects/form.html", form=form, title="Edit Project")


@projects_bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.detail_project", project_id=project_id))
    name = project.name
    payload = build_project_payload(project, "deleted")
    db.session.delete(project)
    db.session.commit()
    fire_webhook("project.deleted", payload)
    flash(f"Project '{name}' deleted.", "success")
    return redirect(url_for("projects.list_projects"))


@projects_bp.route("/<int:project_id>/permissions")
@login_required
def project_permissions(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.detail_project", project_id=project_id))
    permissions = ProjectPermission.query.filter_by(project_id=project_id).all()
    form = PermissionForm()
    form.target_id.choices = []
    return render_template(
        "projects/permissions.html",
        project=project,
        permissions=permissions,
        form=form,
        users=_get_user_pool(),
        groups=_get_group_pool(),
    )


@projects_bp.route("/<int:project_id>/permissions/add", methods=["POST"])
@login_required
def add_permission(project_id):
    project = db.session.get(Project, project_id)
    if not project or not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    target_type = request.form.get("target_type")
    target_id = request.form.get("target_id", type=int)
    level = request.form.get("permission_level", "viewer")
    if not target_id:
        flash("Select a target.", "warning")
        return redirect(url_for("projects.project_permissions", project_id=project_id))
    existing = ProjectPermission.query.filter_by(
        project_id=project_id
    )
    if target_type == "user":
        existing = existing.filter_by(user_id=target_id).first()
    else:
        existing = existing.filter_by(group_id=target_id).first()
    if existing:
        existing.permission_level = level
    else:
        perm = ProjectPermission(
            project_id=project_id,
            user_id=target_id if target_type == "user" else None,
            group_id=target_id if target_type == "group" else None,
            permission_level=level,
        )
        db.session.add(perm)
    db.session.commit()
    flash("Permission updated.", "success")
    return redirect(url_for("projects.project_permissions", project_id=project_id))


@projects_bp.route("/<int:project_id>/permissions/<int:perm_id>/delete", methods=["POST"])
@login_required
def remove_permission(project_id, perm_id):
    project = db.session.get(Project, project_id)
    if not project or not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    perm = db.session.get(ProjectPermission, perm_id)
    if perm and perm.project_id == project_id:
        db.session.delete(perm)
        db.session.commit()
        flash("Permission removed.", "success")
    return redirect(url_for("projects.project_permissions", project_id=project_id))


# ── Labels ──────────────────────────────────────

class LabelForm(FlaskForm):
    name = StringField("Label Name", validators=[DataRequired()])
    color = SelectField("Color", choices=[(c, c) for c in LABEL_COLORS])


@projects_bp.route("/<int:project_id>/labels")
@login_required
def project_labels(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.detail_project", project_id=project_id))
    labels = Label.query.filter_by(project_id=project_id).order_by(Label.name).all()
    form = LabelForm()
    return render_template(
        "projects/labels.html", project=project, labels=labels, form=form,
        label_colors=LABEL_COLORS,
    )


@projects_bp.route("/<int:project_id>/labels/add", methods=["POST"])
@login_required
def add_label(project_id):
    project = db.session.get(Project, project_id)
    if not project or not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    form = LabelForm()
    if form.validate_on_submit():
        existing = Label.query.filter_by(project_id=project_id, name=form.name.data.strip()).first()
        if existing:
            flash("Label already exists.", "warning")
        else:
            label = Label(project_id=project_id, name=form.name.data.strip(), color=form.color.data)
            db.session.add(label)
            db.session.commit()
            flash(f"Label '{label.name}' created.", "success")
    return redirect(url_for("projects.project_labels", project_id=project_id))


@projects_bp.route("/<int:project_id>/labels/<int:label_id>/delete", methods=["POST"])
@login_required
def delete_label(project_id, label_id):
    project = db.session.get(Project, project_id)
    if not project or not current_user.has_project_permission(project_id, "admin"):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    label = db.session.get(Label, label_id)
    if label and label.project_id == project_id:
        TaskLabel.query.filter_by(label_id=label_id).delete()
        db.session.delete(label)
        db.session.commit()
        flash("Label deleted.", "success")
    return redirect(url_for("projects.project_labels", project_id=project_id))


# ── Activity Log ────────────────────────────────

@projects_bp.route("/<int:project_id>/activity")
@login_required
def project_activity(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        flash("Project not found.", "danger")
        return redirect(url_for("projects.list_projects"))
    if not current_user.has_project_permission(project_id):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    from ..models import ActivityLog
    activities = ActivityLog.query.filter_by(project_id=project_id).order_by(
        ActivityLog.created_at.desc()
    ).limit(100).all()
    return render_template(
        "projects/activity.html", project=project, activities=activities
    )
