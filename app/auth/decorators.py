from functools import wraps
from flask import redirect, url_for, flash, abort
from flask_login import current_user
from ..models import db, Project


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def owner_or_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not (current_user.is_admin or current_user.is_owner):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def project_permission_required(level="viewer"):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            project_id = kwargs.get("project_id") or kwargs.get("id")
            if project_id is None:
                abort(404)
            project = db.session.get(Project, project_id)
            if project is None:
                abort(404)
            if not current_user.can_manage_all_projects() and not current_user.has_project_permission(project_id, level):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def sso_admin_permission(permission_key):
    """Decorator that checks SSO admin permissions matrix.
    Local admins/owners always pass. SSO admins check the specific permission."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if current_user.is_admin or current_user.is_owner:
                return f(*args, **kwargs)
            if current_user.is_sso_admin:
                from ..models import SsoAdminPermissions
                perms = SsoAdminPermissions.query.get(1)
                if perms and getattr(perms, permission_key, False):
                    return f(*args, **kwargs)
            abort(403)
        return decorated_function
    return decorator
