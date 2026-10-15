"""Application factory."""
import logging
import secrets
import threading
import time
from datetime import timedelta

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from core import db, security, utils
from core.errors import ApiError

log = logging.getLogger("app")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' https: data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def seed_admin():
    if any(not a.get("deleted") for a in db.get_list("/admins")):
        return
    pw, generated = Config.ADMIN_PASSWORD, False
    if not pw:
        pw, generated = secrets.token_urlsafe(12) + "a1", True
    problem = security.password_problem(pw)
    aid, username = utils.new_id("adm"), Config.ADMIN_USERNAME
    db.set(f"/admin_usernames/{username.lower()}", aid)
    db.set(f"/admins/{aid}", {"id": aid, "username": username, "username_lower": username.lower(),
                              "password_hash": security.hash_password(pw), "role": "admin", "active": True,
                              "session_version": 1, "created_at": utils.now(), "created_by": "system"})
    log.warning("Created first admin '%s'.", username)
    if generated:
        log.warning("GENERATED ADMIN PASSWORD (shown once, change it in Settings): %s", pw)
    if problem:
        log.warning("The ADMIN_PASSWORD you set is weak (%s). Change it after logging in.", problem)


def _background(app):
    from bot.tg import TelegramError, tg
    from services import payments

    def housekeeping():
        while True:
            time.sleep(30)
            try:
                payments.expire_due()
            except Exception:
                log.exception("expiry sweep failed")

    def register_webhook():
        time.sleep(3)
        if not (Config.BOT_TOKEN and Config.BASE_URL):
            log.warning("BOT_TOKEN / BASE_URL missing: webhook not registered automatically.")
            return
        try:
            tg.set_webhook(f"{Config.BASE_URL}/telegram/{Config.WEBHOOK_PATH_SECRET}", Config.WEBHOOK_HEADER_SECRET)
            log.info("Telegram webhook registered")
        except TelegramError as e:
            log.error("Webhook registration failed: %s", e)

    threading.Thread(target=housekeeping, daemon=True, name="housekeeping").start()
    threading.Thread(target=register_webhook, daemon=True, name="webhook-setup").start()


def create_app(background=True):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        SESSION_COOKIE_NAME="__Host-gzsid" if Config.COOKIE_SECURE else "gzsid",
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=Config.COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=Config.SESSION_HOURS),
        MAX_CONTENT_LENGTH=3 * 1024 * 1024, JSON_SORT_KEYS=False,
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if not Config.SECRET_KEY_FROM_ENV:
        log.warning("SECRET_KEY not set: using a random key (admins are logged out on every restart).")

    db.init()
    seed_admin()

    from routes import bp as admin_bp
    from routes.media_routes import media_bp
    from routes.pages import pages
    from routes.public_api import api_bp
    from routes.webhook import webhook_bp

    for b in (pages, admin_bp, api_bp, media_bp, webhook_bp):
        app.register_blueprint(b)

    @app.after_request
    def secure_headers(resp):
        h = resp.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        if Config.COOKIE_SECURE:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    def wants_json():
        return request.path.startswith(("/api/", "/admin/api/"))

    @app.errorhandler(ApiError)
    def on_api_error(e):
        resp = jsonify(success=False, error=e.message, message=e.message, code=e.code)
        resp.status_code = e.status
        if getattr(e, "retry_after", None):
            resp.headers["Retry-After"] = str(e.retry_after)
        return resp

    for code, msg in ((400, "Bad request"), (401, "Unauthorized"), (403, "Forbidden"), (404, "Not found"),
                      (405, "Method not allowed"), (413, "File too large (max 3 MB)")):
        def handler(e, code=code, msg=msg):
            if wants_json():
                return jsonify(success=False, error=msg, message=msg, code=str(code)), code
            return msg, code
        app.register_error_handler(code, handler)

    @app.errorhandler(Exception)
    def on_crash(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        log.exception("Unhandled error")
        if wants_json():
            return jsonify(success=False, error="Server error", message="Server error", code="SERVER_ERROR"), 500
        return "Server error", 500

    if background:
        _background(app)
    return app


if __name__ == "__main__":  # local dev:  APP_ENV=development USE_MEMORY_DB=1 python app.py
    create_app().run(host="127.0.0.1", port=5000, debug=False)
