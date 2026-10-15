"""Audit log. The IP address is stored but never returned by the API / shown in the UI."""
import logging

from flask import g, has_request_context

from core import db, utils
from core.security import client_ip

log = logging.getLogger("audit")


def log_action(action, details="", target="", source=None, actor=None):
    """Record who did what. Inside a panel request the admin comes from `g`; elsewhere pass `actor`."""
    admin, ip = None, ""
    if has_request_context():
        admin = getattr(g, "admin", None)
        ip = client_ip()
    if admin:
        who_id, who, src = admin["id"], admin["username"], source or "panel"
    else:
        who_id, who, src = "", actor or "system", source or "system"
    lid = utils.ts_id()
    try:
        db.set(f"/logs/{lid}", {
            "id": lid, "ts": utils.now(), "admin_id": who_id, "admin": who, "source": src,
            "action": str(action)[:80], "target": str(target)[:120], "details": str(details)[:500],
            "ip": ip,  # stored for forensics, stripped from API responses
        })
    except Exception:  # never break a request because logging failed
        log.exception("audit log failed")
