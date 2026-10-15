import hmac
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, abort, request

from config import Config

log = logging.getLogger("webhook")
webhook_bp = Blueprint("webhook", __name__)
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot")
_seen = deque(maxlen=1000)


def _run(update):
    from bot.handlers import process_update

    try:
        process_update(update)
    except Exception:
        log.exception("update handler crashed")


@webhook_bp.post("/telegram/<secret>")
def telegram_webhook(secret):
    if not hmac.compare_digest(secret, Config.WEBHOOK_PATH_SECRET):
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
                               Config.WEBHOOK_HEADER_SECRET):
        abort(403)
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        abort(400)
    uid = update.get("update_id")
    if uid in _seen:  # Telegram retries -> de-duplicate
        return "ok"
    _seen.append(uid)
    _pool.submit(_run, update)  # answer Telegram immediately, work in the background
    return "ok"
