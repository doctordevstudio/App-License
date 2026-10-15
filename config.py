"""Central configuration. Everything comes from environment variables."""
import hashlib
import os
import secrets

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _flag(name, default=False):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    ENV = os.getenv("APP_ENV", "production").lower()
    IS_DEV = ENV == "development"

    # ---- security ----
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_FROM_ENV = bool(os.getenv("SECRET_KEY"))
    COOKIE_SECURE = _flag("COOKIE_SECURE", not IS_DEV)
    SESSION_HOURS = _int("SESSION_HOURS", 8)

    # first admin (created automatically when the database has no admins)
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

    # ---- firebase ----
    FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
    FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    FIREBASE_CREDENTIALS_FILE = os.getenv("FIREBASE_CREDENTIALS_FILE", "serviceAccountKey.json")
    USE_MEMORY_DB = _flag("USE_MEMORY_DB", False)  # local testing only

    # ---- telegram ----
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")  # can also be set from the panel
    BASE_URL = (os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    _ws = os.getenv("WEBHOOK_SECRET") or (SECRET_KEY + BOT_TOKEN)
    WEBHOOK_PATH_SECRET = hashlib.sha256(("path" + _ws).encode()).hexdigest()[:40]
    WEBHOOK_HEADER_SECRET = hashlib.sha256(("hdr" + _ws).encode()).hexdigest()[:40]

    # ---- business rules ----
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
    TXN_TTL_SECONDS = _int("TXN_TTL_SECONDS", 600)  # 10 minutes
    MAX_TXN_PER_DAY = _int("MAX_TXN_PER_DAY", 5)
    PAGE_SIZE = 20

    # ---- rate limits (requests / seconds) ----
    API_IP_LIMIT = (_int("API_IP_LIMIT", 60), 60)  # per IP per minute
    API_KEY_LIMIT = (_int("API_LICENSE_LIMIT", 20), 60)  # per license key per minute
    API_BAD_LIMIT = (_int("API_BAD_LIMIT", 10), 60)  # invalid-license guesses per IP per minute
    ADMIN_LIMIT = (_int("ADMIN_LIMIT", 600), 60)
    LOGIN_LIMIT = (10, 60)
