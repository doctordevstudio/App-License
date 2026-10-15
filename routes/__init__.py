"""Admin panel JSON API (everything under /admin/api). One guard protects every route."""
from flask import Blueprint, g, jsonify, request

from core import security
from core.cache import cache
from core.errors import ApiError

bp = Blueprint("admin_api", __name__, url_prefix="/admin/api")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
VIEWER_OK = {"admin_api.logout", "admin_api.change_password"}  # view-only users may still do these


@bp.before_request
def guard():
    if request.endpoint in ("admin_api.static",):
        return None
    g.admin = security.current_admin()
    if not g.admin:
        return jsonify(error="Unauthorized"), 401
    security.rate_limit_or_429(f"adm:{g.admin['id']}", security.Config.ADMIN_LIMIT)
    if request.method in UNSAFE:
        if not security.csrf_valid(request.headers.get("X-CSRF-Token")):
            return jsonify(error="Invalid CSRF token. Reload the page."), 403
        if g.admin["role"] != "admin" and request.endpoint not in VIEWER_OK:
            return jsonify(error="View-only account: you cannot change anything."), 403
    return None


@bp.after_request
def after(resp):
    resp.headers["Cache-Control"] = "no-store"
    if request.method in UNSAFE and resp.status_code < 400:
        cache.clear()
    return resp


@bp.errorhandler(ApiError)
def api_error(e):
    resp = jsonify(error=e.message, code=e.code)
    resp.status_code = e.status
    if getattr(e, "retry_after", None):
        resp.headers["Retry-After"] = str(e.retry_after)
    return resp


def require_admin_role():
    if g.admin["role"] != "admin":
        raise ApiError("Only the main admin can do this.", 403)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Invalid request body.")
    return data


from . import (admins, apps, auth_api, earnings, licenses, logs, plans, settings, upi, users)  # noqa: E402,F401
