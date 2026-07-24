import hashlib
import json
import secrets

from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SelectField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError

from ..models import (
    db, User, Group, GroupMembership,
    SsoSettings, SsoAdminPermissions,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------

class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember Me")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.meta.csrf = False


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


class ProfileForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    current_password = PasswordField("Current Password")
    new_password = PasswordField("New Password", validators=[Length(min=6)])
    confirm_password = PasswordField("Confirm Password", validators=[EqualTo("new_password", message="Passwords must match")])


class SsoSettingsForm(FlaskForm):
    enabled = BooleanField("Enable SSO")
    provider_name = StringField("Provider Name", validators=[Length(max=50)])
    client_id = StringField("Client ID", validators=[Length(max=255)])
    client_secret = StringField("Client Secret", validators=[Length(max=255)])
    discovery_url = StringField("Discovery URL", validators=[Length(max=500)])
    scopes = StringField("Scopes", validators=[Length(max=255)])
    group_claim = StringField("Group Claim Key", validators=[Length(max=100)])
    group_prefix = StringField("Group Prefix Filter", validators=[Length(max=50)])
    auto_create_users = BooleanField("Auto-Create Users on First Login")
    jit_group_sync = BooleanField("Sync Groups on Every Login")
    auto_create_groups = BooleanField("Auto-Create Taskit Groups")
    admin_group_name = StringField("Admin Group (OIDC)", validators=[Length(max=255)])
    allowed_groups = StringField("Allowed Groups (comma-separated)", validators=[Length(max=500)])


class SsoAdminPermsForm(FlaskForm):
    manage_users = BooleanField("Manage Users")
    manage_groups = BooleanField("Manage Groups")
    manage_projects = BooleanField("Manage Projects")
    manage_settings = BooleanField("Manage Settings")
    manage_webhooks = BooleanField("Manage Webhooks")
    manage_sso = BooleanField("Manage SSO")
    view_activity_log = BooleanField("View Activity Log")
    delete_projects = BooleanField("Delete Projects")
    manage_admins = BooleanField("Manage Admins")


# ---------------------------------------------------------------------------
# Local Auth
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.password_hash and user.check_password(form.password.data) and user.is_active_user:
            login_user(user, remember=form.remember.data)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))
        flash("Invalid username or password.", "danger")
    return render_template("auth/login.html", form=form)

auth_bp._csrf_exempt = [login]


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


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        if form.current_password.data and current_user.password_hash and not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "error")
        else:
            current_user.email = form.email.data
            if form.new_password.data:
                current_user.set_password(form.new_password.data)
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


# ---------------------------------------------------------------------------
# SSO / OIDC
# ---------------------------------------------------------------------------

def _get_oidc_provider(sso_settings):
    """Build an authlib OIDC client from SsoSettings."""
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth()
    oauth.register(
        name="taskit_sso",
        client_id=sso_settings.client_id,
        client_secret=sso_settings.client_secret,
        server_metadata_url=sso_settings.discovery_url,
        client_kwargs={"scope": sso_settings.scopes},
    )
    return oauth.create_client("taskit_sso")


def _parse_oidc_groups(claims, group_claim, group_prefix):
    """Extract and filter OIDC groups from claims."""
    raw = claims.get(group_claim, [])
    if isinstance(raw, str):
        raw = [g.strip() for g in raw.split(",") if g.strip()]
    if not isinstance(raw, list):
        raw = []
    if group_prefix:
        prefix = group_prefix.rstrip("-")
        raw = [g for g in raw if g.startswith(prefix + "-") or g == prefix]
    return raw


def _sync_groups(user, oidc_groups, sso_settings):
    """JIT sync: match OIDC groups to Taskit groups, sync memberships."""
    current_membership_ids = set()
    current_group_names = set()

    for m in user.group_memberships:
        current_group_names.add(m.group.name)

    for raw_group in oidc_groups:
        prefix = sso_settings.group_prefix.rstrip("-")
        if raw_group.startswith(prefix + "-"):
            taskit_name = raw_group[len(prefix) + 1:]
        else:
            taskit_name = raw_group

        group = Group.query.filter_by(name=taskit_name).first()
        if not group:
            if sso_settings.auto_create_groups:
                group = Group(name=taskit_name, description=f"Auto-created from SSO group: {raw_group}")
                db.session.add(group)
                db.session.flush()
            else:
                continue

        existing = GroupMembership.query.filter_by(user_id=user.id, group_id=group.id).first()
        if not existing:
            membership = GroupMembership(user_id=user.id, group_id=group.id, role="member")
            db.session.add(membership)
        current_membership_ids.add(group.id)

    if sso_settings.jit_group_sync:
        for m in list(user.group_memberships):
            if m.group_id not in current_membership_ids:
                db.session.delete(m)


def _check_allowed_groups(user, oidc_groups, sso_settings):
    """Check if user is in at least one allowed group. Returns True if allowed."""
    if not sso_settings.allowed_groups:
        return True
    allowed = [g.strip() for g in sso_settings.allowed_groups.split(",") if g.strip()]
    if not allowed:
        return True
    return bool(set(oidc_groups) & set(allowed))


def _check_sso_admin(user, oidc_groups, sso_settings):
    """Set/clear is_sso_admin based on admin group membership."""
    if sso_settings.admin_group_name and sso_settings.admin_group_name in oidc_groups:
        user.is_sso_admin = True
    else:
        user.is_sso_admin = False


@auth_bp.route("/sso/login")
def sso_login():
    sso_settings = SsoSettings.query.get(1)
    if not sso_settings or not sso_settings.enabled:
        flash("SSO is not configured.", "warning")
        return redirect(url_for("auth.login"))

    try:
        client = _get_oidc_provider(sso_settings)
    except Exception as e:
        flash(f"SSO configuration error: {e}", "danger")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("auth.sso_callback", _external=True)
    state = secrets.token_urlsafe(32)
    session["oidc_state"] = state
    return client.authorize_redirect(redirect_uri, state=state)


@auth_bp.route("/sso/callback")
def sso_callback():
    sso_settings = SsoSettings.query.get(1)
    if not sso_settings or not sso_settings.enabled:
        flash("SSO is not configured.", "warning")
        return redirect(url_for("auth.login"))

    # Verify state
    expected_state = session.pop("oidc_state", None)
    received_state = request.args.get("state")
    if not expected_state or expected_state != received_state:
        flash("SSO login failed: invalid state parameter.", "danger")
        return redirect(url_for("auth.login"))

    try:
        client = _get_oidc_provider(sso_settings)
        token = client.authorize_access_token()
        userinfo = token.get("userinfo") or {}
        if not userinfo:
            userinfo = client.userinfo(token=token)
    except Exception as e:
        flash(f"SSO authentication failed: {e}", "danger")
        return redirect(url_for("auth.login"))

    sub = userinfo.get("sub")
    email = userinfo.get("email", "")
    if not sub:
        flash("SSO login failed: no subject claim.", "danger")
        return redirect(url_for("auth.login"))

    # Parse groups
    oidc_groups = _parse_oidc_groups(userinfo, sso_settings.group_claim, sso_settings.group_prefix)

    # Check allowed groups before doing anything else
    if not _check_allowed_groups(None, oidc_groups, sso_settings):
        flash("Your account is not authorized to access Taskit.", "danger")
        return redirect(url_for("auth.login"))

    # Find or create user
    user = User.query.filter_by(oidc_sub=sub).first()
    if not user:
        if not sso_settings.auto_create_users:
            flash("Account not found. Contact an administrator.", "danger")
            return redirect(url_for("auth.login"))
        # Derive username from email or preferred_username
        username = userinfo.get("preferred_username", "")
        if not username and email:
            username = email.split("@")[0]
        if not username:
            username = f"sso-{sub[:12]}"
        # Ensure unique
        base = username
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f"{base}-{counter}"
            counter += 1
        user = User(
            username=username,
            email=email or f"{sub}@sso.local",
            oidc_sub=sub,
            auth_method="oidc",
            is_active_user=True,
        )
        db.session.add(user)
        db.session.flush()

    # Update email if changed
    if email and user.email != email:
        # Check uniqueness
        existing = User.query.filter(User.email == email, User.id != user.id).first()
        if not existing:
            user.email = email

    # Update OIDC sub if not set (e.g. user was created locally then SSO enabled)
    if not user.oidc_sub:
        existing_sub = User.query.filter(User.oidc_sub == sub, User.id != user.id).first()
        if not existing_sub:
            user.oidc_sub = sub
            user.auth_method = "oidc"

    # Check active
    if not user.is_active_user:
        flash("Your account has been disabled. Contact an administrator.", "danger")
        return redirect(url_for("auth.login"))

    # Check group prefix filter for login requirement
    if not _check_allowed_groups(user, oidc_groups, sso_settings):
        flash("Your account is not authorized to access Taskit.", "danger")
        return redirect(url_for("auth.login"))

    # JIT group sync
    if sso_settings.jit_group_sync:
        _sync_groups(user, oidc_groups, sso_settings)

    # SSO admin check
    _check_sso_admin(user, oidc_groups, sso_settings)

    # Track groups hash for change detection
    groups_str = json.dumps(sorted(oidc_groups))
    user.oidc_groups_hash = hashlib.sha256(groups_str.encode()).hexdigest()

    db.session.commit()
    login_user(user, remember=True)
    next_page = request.args.get("next")
    return redirect(next_page or url_for("dashboard.index"))


# ---------------------------------------------------------------------------
# Admin: Users
# ---------------------------------------------------------------------------

@auth_bp.route("/admin/users")
@login_required
def admin_users():
    from .decorators import sso_admin_permission
    if not current_user.can_manage_all_projects() and not (current_user.is_sso_admin and SsoAdminPermissions.get().manage_users):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@auth_bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
def admin_edit_user(user_id):
    from .decorators import sso_admin_permission
    if not current_user.can_manage_all_projects() and not (current_user.is_sso_admin and SsoAdminPermissions.get().manage_users):
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


# ---------------------------------------------------------------------------
# Admin: Groups
# ---------------------------------------------------------------------------

@auth_bp.route("/admin/groups")
@login_required
def admin_groups():
    from .decorators import sso_admin_permission
    if not current_user.can_manage_all_projects() and not (current_user.is_sso_admin and SsoAdminPermissions.get().manage_groups):
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))
    groups = Group.query.order_by(Group.name).all()
    return render_template("admin/groups.html", groups=groups)


@auth_bp.route("/admin/groups/create", methods=["GET", "POST"])
@login_required
def admin_create_group():
    from .decorators import sso_admin_permission
    if not current_user.can_manage_all_projects() and not (current_user.is_sso_admin and SsoAdminPermissions.get().manage_groups):
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
    from .decorators import sso_admin_permission
    if not current_user.can_manage_all_projects() and not (current_user.is_sso_admin and SsoAdminPermissions.get().manage_groups):
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
    from .decorators import sso_admin_permission
    if not current_user.can_manage_all_projects() and not (current_user.is_sso_admin and SsoAdminPermissions.get().manage_groups):
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


# ---------------------------------------------------------------------------
# Admin: SSO Settings
# ---------------------------------------------------------------------------

@auth_bp.route("/admin/sso", methods=["GET", "POST"])
@login_required
def admin_sso():
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))

    sso_settings = SsoSettings.query.get(1)
    if not sso_settings:
        sso_settings = SsoSettings(id=1)
        db.session.add(sso_settings)
        db.session.commit()

    form = SsoSettingsForm(obj=sso_settings)

    if form.validate_on_submit():
        sso_settings.enabled = form.enabled.data
        sso_settings.provider_name = form.provider_name.data or "Authentik"
        sso_settings.client_id = form.client_id.data or ""
        sso_settings.client_secret = form.client_secret.data or ""
        sso_settings.discovery_url = form.discovery_url.data or ""
        sso_settings.scopes = form.scopes.data or "openid email profile groups"
        sso_settings.group_claim = form.group_claim.data or "groups"
        sso_settings.group_prefix = form.group_prefix.data or "taskit-"
        sso_settings.auto_create_users = form.auto_create_users.data
        sso_settings.jit_group_sync = form.jit_group_sync.data
        sso_settings.auto_create_groups = form.auto_create_groups.data
        sso_settings.admin_group_name = form.admin_group_name.data or ""
        sso_settings.allowed_groups = form.allowed_groups.data or ""
        db.session.commit()
        flash("SSO settings updated.", "success")
        return redirect(url_for("auth.admin_sso"))

    return render_template("admin/sso.html", form=form, sso_settings=sso_settings)


@auth_bp.route("/admin/sso/permissions", methods=["GET", "POST"])
@login_required
def admin_sso_permissions():
    if not current_user.can_manage_all_projects():
        flash("Access denied.", "danger")
        return redirect(url_for("projects.list_projects"))

    perms = SsoAdminPermissions.query.get(1)
    if not perms:
        perms = SsoAdminPermissions(id=1)
        db.session.add(perms)
        db.session.commit()

    form = SsoAdminPermsForm(obj=perms)

    if form.validate_on_submit():
        perms.manage_users = form.manage_users.data
        perms.manage_groups = form.manage_groups.data
        perms.manage_projects = form.manage_projects.data
        perms.manage_settings = form.manage_settings.data
        perms.manage_webhooks = form.manage_webhooks.data
        perms.manage_sso = form.manage_sso.data
        perms.view_activity_log = form.view_activity_log.data
        perms.delete_projects = form.delete_projects.data
        perms.manage_admins = form.manage_admins.data
        db.session.commit()
        flash("SSO admin permissions updated.", "success")
        return redirect(url_for("auth.admin_sso_permissions"))

    sso_admins = User.query.filter_by(is_sso_admin=True).all()
    return render_template(
        "admin/sso_permissions.html",
        form=form,
        sso_admins=sso_admins,
    )
