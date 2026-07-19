from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SelectField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError

from ..models import db, User, Group, GroupMembership

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember Me")


class RegisterForm(FlaskForm):
    username = StringField(
        "Username", validators=[DataRequired(), Length(min=3, max=80)]
    )
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=6)]
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match")],
    )

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("Username already taken.")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError("Email already registered.")


class UserEditForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    is_admin = BooleanField("Admin")
    is_owner = BooleanField("Owner")
    is_active_user = BooleanField("Active")
    password = PasswordField("New Password (leave blank to keep current)")


class GroupForm(FlaskForm):
    name = StringField("Group Name", validators=[DataRequired(), Length(min=2, max=80)])
    description = StringField("Description")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data) and user.is_active_user:
            login_user(user, remember=form.remember.data)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))
        flash("Invalid username or password.", "danger")
    return render_template("auth/login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            is_admin=False,
            is_owner=False,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash("Account created. You can now log in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


class ProfileForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    current_password = PasswordField("Current Password")
    new_password = PasswordField("New Password", validators=[Length(min=6)])
    confirm_password = PasswordField("Confirm Password", validators=[EqualTo("new_password", message="Passwords must match")])


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        if form.current_password.data and not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "error")
        else:
            current_user.email = form.email.data
            if form.new_password.data:
                current_user.set_password(form.new_password.data)
            from ..models import db
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", form=form)


THEMES = ["dark", "light", "midnight", "ocean", "forest", "sunset", "rose", "ember", "lavender", "arctic", "neon", "amber"]


@auth_bp.route("/theme/<theme_name>", methods=["POST"])
@login_required
def set_theme(theme_name):
    if theme_name in THEMES:
        current_user.theme = theme_name
        db.session.commit()
    return {"ok": True}


@auth_bp.route("/admin/users")
@login_required
def admin_users():
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@auth_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_user(user_id):
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("auth.admin_users"))
    form = UserEditForm(obj=user)
    if form.validate_on_submit():
        user.username = form.username.data
        user.email = form.email.data
        user.is_admin = form.is_admin.data
        user.is_owner = form.is_owner.data
        user.is_active_user = form.is_active_user.data
        if form.password.data:
            user.set_password(form.password.data)
        db.session.commit()
        flash(f"User {user.username} updated.", "success")
        return redirect(url_for("auth.admin_users"))
    return render_template("admin/edit_user.html", form=form, target_user=user)


@auth_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("auth.admin_users"))
    if user.id == current_user.id:
        flash("Cannot delete yourself.", "danger")
        return redirect(url_for("auth.admin_users"))
    db.session.delete(user)
    db.session.commit()
    flash(f"User {user.username} deleted.", "success")
    return redirect(url_for("auth.admin_users"))


@auth_bp.route("/admin/groups")
@login_required
def admin_groups():
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    groups = Group.query.order_by(Group.name).all()
    return render_template("admin/groups.html", groups=groups)


@auth_bp.route("/admin/groups/create", methods=["GET", "POST"])
@login_required
def admin_create_group():
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    form = GroupForm()
    if form.validate_on_submit():
        group = Group(name=form.name.data, description=form.description.data)
        db.session.add(group)
        db.session.commit()
        flash(f"Group '{group.name}' created.", "success")
        return redirect(url_for("auth.admin_groups"))
    return render_template("admin/group_form.html", form=form, title="Create Group")


@auth_bp.route("/admin/groups/<int:group_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_group(group_id):
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    group = db.session.get(Group, group_id)
    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("auth.admin_groups"))
    form = GroupForm(obj=group)
    if form.validate_on_submit():
        group.name = form.name.data
        group.description = form.description.data
        db.session.commit()
        flash(f"Group '{group.name}' updated.", "success")
        return redirect(url_for("auth.admin_groups"))
    return render_template("admin/group_form.html", form=form, title="Edit Group")


@auth_bp.route("/admin/groups/<int:group_id>/delete", methods=["POST"])
@login_required
def admin_delete_group(group_id):
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    group = db.session.get(Group, group_id)
    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("auth.admin_groups"))
    db.session.delete(group)
    db.session.commit()
    flash(f"Group '{group.name}' deleted.", "success")
    return redirect(url_for("auth.admin_groups"))


@auth_bp.route("/admin/groups/<int:group_id>/members", methods=["GET", "POST"])
@login_required
def admin_group_members(group_id):
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    group = db.session.get(Group, group_id)
    if not group:
        flash("Group not found.", "danger")
        return redirect(url_for("auth.admin_groups"))

    if request.method == "POST":
        user_id = request.form.get("user_id", type=int)
        action = request.form.get("action")
        if action == "add" and user_id:
            existing = GroupMembership.query.filter_by(
                user_id=user_id, group_id=group_id
            ).first()
            if not existing:
                membership = GroupMembership(
                    user_id=user_id, group_id=group_id, role="member"
                )
                db.session.add(membership)
                db.session.commit()
                flash("User added to group.", "success")
        elif action == "remove" and user_id:
            membership = GroupMembership.query.filter_by(
                user_id=user_id, group_id=group_id
            ).first()
            if membership:
                db.session.delete(membership)
                db.session.commit()
                flash("User removed from group.", "success")
        return redirect(url_for("auth.admin_group_members", group_id=group_id))

    members = User.query.join(GroupMembership).filter(
        GroupMembership.group_id == group_id
    ).all()
    non_members = User.query.filter(
        ~User.id.in_(
            db.session.query(GroupMembership.user_id).filter_by(group_id=group_id)
        )
    ).all()
    return render_template(
        "admin/group_members.html",
        group=group,
        members=members,
        non_members=non_members,
    )
