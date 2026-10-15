"""Notifications to the admin chat (and the UPI owner chat)."""
import logging

from bot import messages as m
from bot.tg import TelegramError, tg
from services import settings

log = logging.getLogger("notify")


def admin_chat():
    return settings.bot_settings()["admin_chat_id"]


def recipients(*extra):
    seen, out = set(), []
    for c in (*extra, admin_chat()):
        c = str(c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def send_to(chats, text, photo=None, reply_markup=None, reply_to=None):
    """Send to many chats. Returns {chat_id: message_id} for those that succeeded.
    `reply_to` is a {chat_id: message_id} map used to thread the message under an earlier one."""
    sent = {}
    for chat in chats:
        rt = (reply_to or {}).get(chat)
        try:
            if photo:
                try:
                    msg = tg.send_photo(chat, photo, text[:1024], reply_markup, rt)
                except TelegramError as e:
                    log.warning("photo to %s failed (%s), falling back to text", chat, e)
                    msg = tg.send_message(chat, text, reply_markup, reply_to=rt)
            else:
                msg = tg.send_message(chat, text, reply_markup, reply_to=rt)
            sent[chat] = msg["message_id"]
        except TelegramError as e:
            log.warning("notify %s failed: %s", chat, e)
    return sent


def new_user(user):
    text = "🙋 <b>New user started the bot</b>\n\n" + m.user_block(user)
    send_to(recipients(), text, photo=user.get("profile_file_id") or None)
