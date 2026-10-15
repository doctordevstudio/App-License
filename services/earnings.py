"""Earnings ledger. One row per successful payment / manual license with an amount."""
from core import db, utils


def record(*, app, amount, source, user, license_key, txn_id="", upi_id="", plan_id="", label=""):
    eid = utils.ts_id()
    row = {
        "id": eid, "ts": utils.now(), "app_id": app["id"], "app_name": app.get("name", ""),
        "amount": amount, "source": source, "license_key": license_key, "txn_id": txn_id,
        "upi_id": upi_id, "plan_id": plan_id, "label": label,
        "user": {
            "chat_id": str(user.get("chat_id", "")), "full_name": user.get("full_name", ""),
            "username": user.get("username", ""), "profile_image_url": user.get("profile_image_url", ""),
        },
    }
    db.set(f"/earnings/{eid}", row)
    return row


def ledger_all():
    return db.get_list("/earnings")
