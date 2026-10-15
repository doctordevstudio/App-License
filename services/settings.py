from config import Config
from core import db, utils
from core.cache import cache
from core.errors import ApiError

DEFAULT_WELCOME = (
    "👋 <b>Welcome, {first_name}!</b>\n\n"
    "Get your license key in a few taps — pick an app, choose a plan and pay with UPI.\n\n"
    "Tap <b>Purchase License Key</b> below to begin 👇"
)


def bot_settings():
    hit = cache.get("bot_settings")
    if hit is not None:
        return hit
    s = db.get("/settings/bot") or {}
    out = {
        "welcome_message": s.get("welcome_message") or DEFAULT_WELCOME,
        "welcome_format": s.get("welcome_format") or "html",
        "support_username": s.get("support_username") or "",
        "admin_chat_id": str(s.get("admin_chat_id") or Config.ADMIN_CHAT_ID or ""),
    }
    cache.set("bot_settings", out, 5)
    return out


def save_bot_settings(data):
    fmt = data.get("welcome_format", "html")
    if fmt not in ("html", "text"):
        raise ApiError("Welcome format must be html or text.")
    admin_chat = str(data.get("admin_chat_id") or "").strip()
    if admin_chat:
        admin_chat = utils.clean_chat_id(admin_chat, "Admin chat ID")
    support = utils.clean_text(data.get("support_username"), "Support username", 64, False).lstrip("@")
    if support and not support.replace("_", "").isalnum():
        raise ApiError("Support username is invalid.")
    msg = utils.clean_text(data.get("welcome_message"), "Welcome message", 3500)
    db.update("/settings/bot", {"welcome_message": msg, "welcome_format": fmt,
                                "support_username": support, "admin_chat_id": admin_chat})
    cache.clear()


# ---------------------------------------------------------------- channels
def channels(include_inactive=False):
    rows = [c for c in db.get_list("/channels") if not c.get("deleted")]
    rows.sort(key=lambda c: c.get("created_at", 0))
    return rows


def channel_join_counts(channel_id, start, end):
    joins = db.get(f"/channel_joins/{channel_id}", {}) or {}
    return sum(1 for ts in joins.values() if isinstance(ts, (int, float)) and start <= ts <= end)


# ---------------------------------------------------------------- maintenance
def maintenance_rules():
    return [r for r in db.get_list("/maintenance") if not r.get("deleted")]


def active_maintenance(app_id):
    """Return the enabled maintenance rule that applies to the app (app-specific wins over 'all')."""
    hit = cache.get(("maint", app_id))
    if hit is not None:
        return hit[0]
    rules = [r for r in maintenance_rules() if r.get("enabled")]
    chosen = next((r for r in rules if r.get("app_id") == app_id), None) or \
        next((r for r in rules if r.get("app_id") == "all"), None)
    cache.set(("maint", app_id), (chosen,), 5)
    return chosen
