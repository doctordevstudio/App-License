import time

from flask import Blueprint, g, make_response, redirect, render_template, request, session

from config import Config
from core import db, security, utils
from core.audit import log_action

pages = Blueprint("pages", __name__)


def _login_page(error=None, status=200):
    resp = make_response(render_template("login.html", csrf=security.csrf_token(), error=error), status)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@pages.get("/")
def index():
    if not security.current_admin():
        return redirect("/login")
    resp = make_response(render_template("app.html"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@pages.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if security.current_admin():
            return redirect("/")
        return _login_page()

    ip = security.client_ip()
    if not security.csrf_valid(request.form.get("csrf_token")):
        return _login_page("Session expired. Please try again.", 400)
    ok, _ = security.limiter.hit(f"login:{ip}", *Config.LOGIN_LIMIT)
    if not ok:
        return _login_page("Too many attempts. Wait a minute.", 429)

    username = (request.form.get("username") or "").strip()[:64]
    password = (request.form.get("password") or "")[:256]
    wait = security.login_locked_for(username, ip)
    if wait:
        return _login_page(f"Too many failed attempts. Try again in {wait // 60 + 1} min.", 429)

    aid = db.get(f"/admin_usernames/{username.lower()}") if username else None
    adm = db.get(f"/admins/{aid}") if aid else None
    usable = bool(adm) and not adm.get("deleted") and adm.get("active", True)
    if usable:
        good = security.verify_password(adm["password_hash"], password)
    else:
        security.burn_password_check(password)
        good = False
    if not good:
        security.login_failed(username, ip)
        log_action("auth.login_failed", f"username tried: {username[:32]}", source="panel", actor="(anonymous)")
        time.sleep(0.4)
        return _login_page("Invalid username or password.", 401)

    session.clear()  # prevent session fixation
    session.permanent = True
    session["aid"] = adm["id"]
    session["sv"] = adm.get("session_version", 1)
    security.csrf_token()
    security.login_succeeded(username, ip)
    db.update(f"/admins/{adm['id']}", {"last_login": utils.now()})
    g.admin = adm
    log_action("auth.login", "")
    return redirect("/")


@pages.get("/healthz")
def healthz():
    return "ok", 200, {"Cache-Control": "no-store"}


@pages.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}
