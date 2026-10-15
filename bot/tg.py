"""Minimal Telegram Bot API client (plain HTTPS, no heavy framework)."""
import json
import logging

import requests

from config import Config

log = logging.getLogger("tg")


class TelegramError(Exception):
    def __init__(self, description, code=None):
        super().__init__(description)
        self.description = description
        self.code = code


def _encode(payload):
    return {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in (payload or {}).items()
            if v is not None}


class Telegram:
    def __init__(self):
        self.session = requests.Session()

    @property
    def token(self):
        return Config.BOT_TOKEN

    def call(self, method, payload=None, files=None, timeout=20):
        if not self.token:
            raise TelegramError("BOT_TOKEN is not configured")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        payload = {k: v for k, v in (payload or {}).items() if v is not None}
        try:
            if files:
                r = self.session.post(url, data=_encode(payload), files=files, timeout=timeout)
            else:
                r = self.session.post(url, json=payload, timeout=timeout)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise TelegramError(f"network error: {e}")
        if not data.get("ok"):
            raise TelegramError(data.get("description", "unknown error"), data.get("error_code"))
        return data["result"]

    # ---- sending ----
    @staticmethod
    def _reply(reply_to):
        return {"message_id": int(reply_to), "allow_sending_without_reply": True} if reply_to else None

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML", reply_to=None):
        return self.call("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": parse_mode or None, "reply_markup": reply_markup,
            "reply_parameters": self._reply(reply_to), "link_preview_options": {"is_disabled": True}})

    def send_photo(self, chat_id, photo, caption=None, reply_markup=None, reply_to=None, parse_mode="HTML"):
        """`photo` is a file_id / URL string or a (filename, bytes) tuple."""
        payload = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode if caption else None,
                   "reply_markup": reply_markup, "reply_parameters": self._reply(reply_to)}
        if isinstance(photo, tuple):
            return self.call("sendPhoto", payload, files={"photo": photo})
        payload["photo"] = photo
        return self.call("sendPhoto", payload)

    def edit_text(self, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
        return self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text,
                                             "parse_mode": parse_mode, "reply_markup": reply_markup,
                                             "link_preview_options": {"is_disabled": True}})

    def edit_caption(self, chat_id, message_id, caption, reply_markup=None, parse_mode="HTML"):
        return self.call("editMessageCaption", {"chat_id": chat_id, "message_id": message_id,
                                                "caption": caption, "parse_mode": parse_mode,
                                                "reply_markup": reply_markup})

    def answer_callback(self, cb_id, text=None, alert=False):
        try:
            return self.call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text,
                                                     "show_alert": alert or None}, timeout=8)
        except TelegramError:
            return None

    # ---- info ----
    def get_chat_member(self, chat_id, user_id):
        return self.call("getChatMember", {"chat_id": chat_id, "user_id": int(user_id)}, timeout=10)

    def get_chat(self, chat_id):
        return self.call("getChat", {"chat_id": chat_id}, timeout=10)

    def get_me(self):
        return self.call("getMe", timeout=10)

    def profile_file_id(self, user_id):
        r = self.call("getUserProfilePhotos", {"user_id": int(user_id), "limit": 1}, timeout=10)
        if r.get("total_count") and r.get("photos"):
            sizes = r["photos"][0]
            return sizes[min(1, len(sizes) - 1)]["file_id"]
        return ""

    def download(self, file_id):
        """Return (bytes, extension). The bot token never leaves the server."""
        info = self.call("getFile", {"file_id": file_id}, timeout=15)
        path = info.get("file_path", "")
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        try:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            raise TelegramError(f"download failed: {e}")
        return r.content, path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"

    # ---- webhook ----
    def set_webhook(self, url, secret):
        return self.call("setWebhook", {"url": url, "secret_token": secret,
                                        "allowed_updates": ["message", "callback_query"],
                                        "max_connections": 20})

    def webhook_info(self):
        return self.call("getWebhookInfo", timeout=10)


tg = Telegram()
