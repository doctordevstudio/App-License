from flask import g, jsonify, request

from bot import messages as m
from bot.tg import TelegramError, tg
from config import Config
from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import notify
from services import settings as svc
from . import body, bp


def webhook_url():
    base = Config.BASE_URL or request.url_root.rstrip("/")
    return f"{base}/telegram/{Config.WEBHOOK_PATH_SECRET}"


@bp.get("/settings")
def get_settings():
    names = {a["id"]: a["name"] for a in db.get_list("/apps") if not a.get("deleted")}
    rules = [{**r, "app_name": "All apps" if r["app_id"] == "all" else names.get(r["app_id"], "(deleted app)")}
             for r in svc.maintenance_rules()]
    rules.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return jsonify(bot=svc.bot_settings(), maintenance=rules, apps=[{"id": k, "name": v} for k, v in names.items()],
                   bot_configured=bool(Config.BOT_TOKEN), webhook_url_host=Config.BASE_URL or request.url_root.rstrip("/"))


@bp.put("/settings/bot")
def save_bot():
    svc.save_bot_settings(body())
    log_action("settings.bot_saved", "welcome / support / admin chat")
    return jsonify(ok=True)


@bp.post("/settings/bot/test-welcome")
def test_welcome():
    s = svc.bot_settings()
    if not s["admin_chat_id"]:
        raise ApiError("Save an Admin Chat ID first.")
    text, mode = m.render_welcome(s["welcome_message"], s["welcome_format"],
                                  {"first_name": "Test", "full_name": "Test User", "username": "testuser", "chat_id": "0"})
    try:
        tg.send_message(s["admin_chat_id"], text, parse_mode=mode)
    except TelegramError as e:
        raise ApiError(f"Telegram rejected the message: {e.description}")
    return jsonify(ok=True)


@bp.post("/settings/webhook")
def set_webhook():
    if not Config.BOT_TOKEN:
        raise ApiError("BOT_TOKEN is not set on the server.")
    try:
        tg.set_webhook(webhook_url(), Config.WEBHOOK_HEADER_SECRET)
        info = tg.webhook_info()
    except TelegramError as e:
        raise ApiError(f"Telegram error: {e.description}")
    log_action("settings.webhook_set", "")
    return jsonify(pending=info.get("pending_update_count", 0), last_error=info.get("last_error_message", ""))


# ------------------------------------------------------------------ maintenance
def _rule_from(d):
    app_id = str(d.get("app_id") or "")
    if app_id != "all":
        a = db.get(f"/apps/{app_id}")
        if not a or a.get("deleted"):
            raise ApiError("Choose an app or 'All apps'.")
    try:
        code = int(d.get("status_code"))
    except (TypeError, ValueError):
        raise ApiError("Status code must be a number like 200 or 301.")
    if not 200 <= code <= 599:
        raise ApiError("Status code must be between 200 and 599.")
    return {"app_id": app_id, "message": utils.clean_text(d.get("message"), "Message", 300),
            "status_code": code, "enabled": bool(d.get("enabled", True))}


@bp.post("/maintenance")
def add_maintenance():
    rule = _rule_from(body())
    rid = utils.new_id("mnt")
    db.set(f"/maintenance/{rid}", {"id": rid, "created_at": utils.now(), "created_by": g.admin["username"], **rule})
    log_action("maintenance.created", f"{rule['app_id']} -> {rule['status_code']}", target=rid)
    return jsonify(ok=True, id=rid), 201


@bp.patch("/maintenance/<rid>")
def edit_maintenance(rid):
    r = db.get(f"/maintenance/{rid}")
    if not r or r.get("deleted"):
        raise ApiError("Rule not found.", 404)
    d = body()
    if set(d) == {"enabled"}:
        db.update(f"/maintenance/{rid}", {"enabled": bool(d["enabled"])})
        log_action("maintenance.toggled", "on" if d["enabled"] else "off", target=rid)
    else:
        db.update(f"/maintenance/{rid}", _rule_from({**r, **d}))
        log_action("maintenance.edited", "", target=rid)
    return jsonify(ok=True)


@bp.delete("/maintenance/<rid>")
def delete_maintenance(rid):
    db.update(f"/maintenance/{rid}", {"deleted": True, "enabled": False, "deleted_at": utils.now(),
                                      "deleted_by": g.admin["username"]})
    log_action("maintenance.deleted", "", target=rid)
    return jsonify(ok=True)


# ------------------------------------------------------------------ force-join channels
@bp.get("/channels")
def list_channels():
    rng = request.args.get("range", "all")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    chans = svc.channels()
    out = []
    for c in chans:
        out.append({**{k: c.get(k, "") for k in ("id", "chat_id", "title", "username", "invite_link", "created_at")},
                    "joined": svc.channel_join_counts(c["id"], lo, hi),
                    "today": svc.channel_join_counts(c["id"], *utils.resolve_range("today")),
                    "yesterday": svc.channel_join_counts(c["id"], *utils.resolve_range("yesterday")),
                    "all": svc.channel_join_counts(c["id"], *utils.resolve_range("all"))})
    return jsonify(items=out)


@bp.post("/channels")
def add_channel():
    raw = utils.clean_text(body().get("chat_id"), "Channel", 80)
    if not raw.startswith("@") and not raw.lstrip("-").isdigit():
        raw = "@" + raw.split("t.me/")[-1].strip("/")
    try:
        chat = tg.get_chat(raw)
        me = tg.get_me()
        member = tg.get_chat_member(chat["id"], me["id"])
    except TelegramError as e:
        raise ApiError(f"Could not access that channel: {e.description}. Add the bot as an admin first.")
    if member.get("status") not in ("administrator", "creator"):
        raise ApiError("The bot must be an admin of the channel to verify members.")
    if any(str(c.get("chat_id")) == str(chat["id"]) for c in svc.channels()):
        raise ApiError("That channel is already added.")
    uname = chat.get("username", "")
    link = f"https://t.me/{uname}" if uname else (chat.get("invite_link") or "")
    if not link:
        try:
            link = tg.call("exportChatInviteLink", {"chat_id": chat["id"]})
        except TelegramError:
            link = ""
    cid = utils.new_id("ch")
    db.set(f"/channels/{cid}", {"id": cid, "chat_id": str(chat["id"]), "title": chat.get("title", raw),
                                "username": uname, "invite_link": link, "created_at": utils.now(),
                                "created_by": g.admin["username"]})
    log_action("channel.added", chat.get("title", raw), target=cid)
    return jsonify(ok=True, id=cid), 201


@bp.delete("/channels/<cid>")
def delete_channel(cid):
    db.update(f"/channels/{cid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": g.admin["username"]})
    log_action("channel.removed", "", target=cid)
    return jsonify(ok=True)
