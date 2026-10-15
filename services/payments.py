"""Transaction lifecycle: create -> pending -> verification -> success | failed.

Used by both the Telegram bot and the admin panel so behaviour is identical everywhere.
Statuses: pending, verification, failed (fail_type: expired | cancelled | rejected), success.
Nothing is ever deleted; every state change is stored on the txn.
"""
import logging

from bot import messages as m
from bot.tg import TelegramError, tg
from config import Config
from core import db, utils
from core.audit import log_action
from core.cache import cache
from core.errors import ApiError
from services import licenses, media, notify

log = logging.getLogger("payments")


# ------------------------------------------------------------------ helpers
def get_txn(txn_id):
    return db.get(f"/txns/{txn_id}")


def _claim(txn_id, from_statuses, to_status):
    """Atomically move status; returns True only for the caller that won the race."""
    state = {"ok": False}

    def fn(cur):
        state["ok"] = False
        if cur in from_statuses:
            state["ok"] = True
            return to_status
        return cur

    db.transaction(f"/txns/{txn_id}/status", fn)
    return state["ok"]


def screenshot_url(txn):
    fid = (txn.get("screenshot") or {}).get("file_id")
    return f"/media/tg/{fid}" if fid else ""


def public_txn(t):
    rem = max(0, t.get("expires_at", 0) - utils.now()) if t.get("status") == "pending" else 0
    return {
        "id": t["id"], "status": t["status"], "fail_type": t.get("fail_type", ""),
        "fail_reason": t.get("fail_reason", ""), "amount": t["amount"], "app_id": t["app_id"],
        "app_name": t["app_name"], "plan_label": t["plan_label"], "upi_id": t["upi_id"],
        "upi_record_id": t.get("upi_record_id", ""), "utr": t.get("utr", ""),
        "created_at": t["created_at"], "expires_at": t["expires_at"], "remaining": rem,
        "submitted_at": t.get("submitted_at", 0), "verified_at": t.get("verified_at", 0),
        "verified_by": t.get("verified_by", ""), "license_key": t.get("license_key", ""),
        "screenshot_url": screenshot_url(t), "user": t["user"],
    }


# ------------------------------------------------------------------ UPI selection (round robin)
def active_upis():
    rows = [u for u in db.get_list("/upis") if not u.get("deleted") and u.get("active", True)]
    rows.sort(key=lambda u: u.get("created_at", 0))
    return rows


def pick_upi():
    upis = active_upis()
    if not upis:
        return None
    if len(upis) == 1:
        return upis[0]
    counter = db.transaction("/meta/rr_counter", lambda cur: int(cur or 0) + 1)
    return upis[(int(counter) - 1) % len(upis)]


# ------------------------------------------------------------------ create
def create_txn(user, plan_id):
    """Returns (status, txn). status: ok | existing | in_verification | daily_limit | no_upi | unavailable"""
    plan = db.get(f"/plans/{plan_id}")
    if not plan or plan.get("deleted"):
        return "unavailable", None
    app = db.get(f"/apps/{plan['app_id']}")
    if not app or app.get("deleted"):
        return "unavailable", None

    uid = str(user["chat_id"])
    mine = db.query_equal("/txns", "user_id", uid)
    now = utils.now()
    verif = [t for t in mine if t["status"] == "verification"]
    if verif:
        return "in_verification", verif[0]
    pend = [t for t in mine if t["status"] == "pending" and t["expires_at"] > now]
    if pend:
        return "existing", pend[0]
    today = utils.day_key()
    if sum(1 for t in mine if utils.day_key(t["created_at"]) == today) >= Config.MAX_TXN_PER_DAY:
        return "daily_limit", None

    upi = pick_upi()
    if not upi:
        return "no_upi", None
    txn_id = utils.gen_txn_id()
    while db.get(f"/txns/{txn_id}") is not None:
        txn_id = utils.gen_txn_id()
    label = utils.validity_label(plan["validity_value"], plan["validity_unit"])
    txn = {
        "id": txn_id, "user_id": uid, "status": "pending", "amount": plan["amount"],
        "app_id": app["id"], "app_name": app["name"], "plan_id": plan["id"], "plan_label": label,
        "validity_value": plan["validity_value"], "validity_unit": plan["validity_unit"],
        "upi_record_id": upi["id"], "upi_id": upi["upi_id"], "upi_name": upi.get("name", ""),
        "created_at": now, "expires_at": now + Config.TXN_TTL_SECONDS,
        "user": {"chat_id": uid, "full_name": user.get("full_name", ""), "username": user.get("username", ""),
                 "profile_image_url": user.get("profile_image_url", ""),
                 "profile_file_id": user.get("profile_file_id", "")},
    }
    db.set(f"/txns/{txn_id}", txn)
    return "ok", txn


def _qr_photo(upi, txn):
    if upi.get("tg_file_id"):
        return upi["tg_file_id"], False
    if upi.get("scanner_media_id"):
        data, _ = media.get_media(upi["scanner_media_id"])
        if data:
            return ("scanner.png", data), True
    return ("qr.png", media.make_upi_qr(upi["upi_id"], upi.get("name"), txn["amount"], txn["id"])), False


def send_card(txn):
    """Send the QR payment card to the user and (for new txns) notify admins."""
    upi = db.get(f"/upis/{txn['upi_record_id']}") or {"upi_id": txn["upi_id"]}
    photo, cache_id = _qr_photo(upi, txn)
    msg = tg.send_photo(txn["user_id"], photo, m.payment_caption(txn), m.card_markup(txn["id"]))
    if cache_id and upi.get("id"):
        try:
            db.update(f"/upis/{upi['id']}", {"tg_file_id": msg["photo"][-1]["file_id"]})
        except Exception:
            pass
    db.update(f"/txns/{txn['id']}", {"user_msg_id": msg["message_id"]})
    return msg


def notify_created(txn):
    chats = notify.recipients(db.get(f"/upis/{txn['upi_record_id']}/telegram_chat_id"))
    sent = notify.send_to(chats, m.admin_created_text(txn), photo=txn["user"].get("profile_file_id") or None)
    if sent:
        db.update(f"/txns/{txn['id']}", {f"created_msgs/{c}": mid for c, mid in sent.items()})


def _txn_chats(txn):
    return notify.recipients(db.get(f"/upis/{txn.get('upi_record_id', '')}/telegram_chat_id"))


def can_moderate(chat_id, txn):
    return str(chat_id) in set(_txn_chats(txn))


# ------------------------------------------------------------------ proof
def find_duplicate(shot, utr, txn_id):
    """A screenshot / UTR already attached to a txn that is in verification or success is a duplicate.
    (Failed txns release their image/UTR so an honest user can retry.)"""
    def live(ref_id):
        return bool(ref_id) and ref_id != txn_id and db.get(f"/txns/{ref_id}/status") in ("verification", "success")

    if live(db.get(f"/image_hashes/{shot['sha256']}")) or live(db.get(f"/image_hashes/u_{shot['unique_id']}")):
        return "image"
    if live(db.get(f"/utr_index/{utr}")):
        return "utr"
    return None


def submit_proof(txn_id, user_id, shot, utr):
    """Returns 'ok' | 'expired' | 'invalid' | 'image' | 'utr'."""
    txn = get_txn(txn_id)
    if not txn or txn["user_id"] != str(user_id) or txn["status"] != "pending":
        return "invalid"
    if txn["expires_at"] <= utils.now():
        expire_due()
        return "expired"
    dup = find_duplicate(shot, utr, txn_id)
    if dup:
        return dup
    if not _claim(txn_id, {"pending"}, "verification"):
        return "invalid"
    shot = dict(shot, url=f"/media/tg/{shot['file_id']}")
    db.update("/", {
        f"txns/{txn_id}/utr": utr, f"txns/{txn_id}/screenshot": shot, f"txns/{txn_id}/submitted_at": utils.now(),
        f"utr_index/{utr}": txn_id, f"image_hashes/{shot['sha256']}": txn_id,
        f"image_hashes/u_{shot['unique_id']}": txn_id,
    })
    txn = get_txn(txn_id)
    created = txn.get("created_msgs") or {}
    sent = notify.send_to(_txn_chats(txn), m.admin_proof_caption(txn), photo=shot["file_id"],
                          reply_markup=m.admin_buttons(txn_id), reply_to=created)
    if sent:
        db.update(f"/txns/{txn_id}", {f"proof_msgs/{c}": mid for c, mid in sent.items()})
    cache.clear()
    log_action("txn.proof_submitted", f"UTR {utr}", target=txn_id, source="telegram", actor=f"user:{user_id}")
    return "ok"


# ------------------------------------------------------------------ resolution
def _edit_proof_messages(txn, status_line):
    for chat, mid in (txn.get("proof_msgs") or {}).items():
        try:
            tg.edit_caption(chat, mid, m.admin_proof_caption(txn, status_line), reply_markup={"inline_keyboard": []})
        except TelegramError as e:
            log.warning("edit proof msg failed: %s", e)


def _thread_note(txn, text):
    notify.send_to(list((txn.get("created_msgs") or {}).keys()) or _txn_chats(txn), text,
                   reply_to=txn.get("created_msgs"))


def verify_txn(txn_id, actor_name, source="panel"):
    txn = get_txn(txn_id)
    if not txn:
        raise ApiError("Transaction not found.", 404)
    if not _claim(txn_id, {"verification"}, "success"):
        raise ApiError(f"Transaction is already {get_txn(txn_id)['status']}.", 409)
    app = db.get(f"/apps/{txn['app_id']}") or {"id": txn["app_id"], "name": txn["app_name"]}
    u = txn["user"]
    lic = licenses.create_license(
        app=app, chat_id=u["chat_id"], full_name=u["full_name"], username=u.get("username", ""),
        profile_image_url=u.get("profile_image_url", ""), profile_file_id=u.get("profile_file_id", ""),
        amount=txn["amount"], validity_value=txn["validity_value"], validity_unit=txn["validity_unit"],
        source="bot", txn_id=txn_id, plan_id=txn["plan_id"], upi_id=txn["upi_record_id"])
    db.update(f"/txns/{txn_id}", {"license_key": lic["key"], "verified_at": utils.now(),
                                  "verified_by": actor_name, "verified_via": source})
    txn = get_txn(txn_id)
    cache.clear()
    try:
        tg.send_message(txn["user_id"],
                        "✅ <b>Payment verified!</b>\n\n"
                        f"Your license key for <b>{m.esc(txn['app_name'])}</b> is:\n\n<code>{lic['key']}</code>\n\n"
                        f"📦 Plan: {m.esc(txn['plan_label'])}\n⏳ Valid until: {utils.fmt_dt(lic['expires_at'])}\n\n"
                        "Tap the key to copy it. Enjoy! 🎉")
    except TelegramError as e:
        log.warning("could not DM license to %s: %s", txn["user_id"], e)
    _edit_proof_messages(txn, f"✅ <b>Verified</b> by {m.esc(actor_name)}")
    log_action("txn.verified", f"{txn['app_name']} / {utils.inr(txn['amount'])}", target=txn_id,
               source=source, actor=actor_name)
    return lic


def fail_txn(txn_id, reason, actor_name, source="panel", fail_type="rejected", from_statuses=("verification",)):
    txn = get_txn(txn_id)
    if not txn:
        raise ApiError("Transaction not found.", 404)
    reason = utils.clean_text(reason, "Reason", 300, required=fail_type == "rejected")
    if not _claim(txn_id, set(from_statuses), "failed"):
        raise ApiError(f"Transaction is already {get_txn(txn_id)['status']}.", 409)
    db.update(f"/txns/{txn_id}", {"fail_type": fail_type, "fail_reason": reason, "failed_at": utils.now(),
                                  "failed_by": actor_name})
    txn = get_txn(txn_id)
    cache.clear()
    if fail_type == "rejected":
        try:
            tg.send_message(txn["user_id"],
                            "❌ <b>Payment verification failed</b>\n\n"
                            f"🧾 Txn: <code>{txn_id}</code>\n📝 Reason: {m.esc(reason)}\n\n"
                            "You can try again from <b>Apps</b> in the menu.")
        except TelegramError as e:
            log.warning("could not notify user of failure: %s", e)
        _edit_proof_messages(txn, f"❌ <b>Failed</b> by {m.esc(actor_name)}\n📝 {m.esc(reason)}")
        log_action("txn.rejected", reason, target=txn_id, source=source, actor=actor_name)
    else:
        label = "expired" if fail_type == "expired" else "cancelled by user"
        _close_user_card(txn, f"⛔ <b>Payment {label}.</b>")
        _thread_note(txn, f"⛔ Txn <code>{txn_id}</code> {label}.")
        if (db.get(f"/bot_state/{txn['user_id']}") or {}).get("txn_id") == txn_id:
            db.set(f"/bot_state/{txn['user_id']}", None)  # user was mid-upload for a txn that just closed
    return txn


def _close_user_card(txn, text):
    mid = txn.get("user_msg_id")
    if not mid:
        return
    try:
        tg.edit_caption(txn["user_id"], mid, m.payment_caption(txn, text).split("\n\nScan the QR")[0] + f"\n\n{text}",
                        reply_markup={"inline_keyboard": []})
    except TelegramError:
        pass


def expire_due():
    """Mark pending txns whose 10 minutes are over as failed(expired). Safe to call often."""
    now = utils.now()
    n = 0
    for t in db.query_equal("/txns", "status", "pending"):
        if t.get("expires_at", 0) <= now:
            try:
                fail_txn(t["id"], "Payment window expired", "system", source="system",
                         fail_type="expired", from_statuses=("pending",))
                n += 1
            except ApiError:
                pass
    return n


# ------------------------------------------------------------------ search
def search(q):
    q = q.strip()
    found = {}
    up = q.upper()
    if up.startswith("TXN"):
        t = get_txn(up)
        if t:
            found[t["id"]] = t
    if q.isdigit():
        tid = db.get(f"/utr_index/{q}")
        if tid and get_txn(tid):
            found[tid] = get_txn(tid)
        if not found and len(q) != 12:
            for t in db.query_equal("/txns", "user_id", q):
                found[t["id"]] = t
    return list(found.values())
