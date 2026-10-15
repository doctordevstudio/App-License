import re

from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import media, payments, stats
from . import body, bp

UPI_RE = re.compile(r"^[A-Za-z0-9._\-]{2,64}@[A-Za-z][A-Za-z0-9]{1,32}$")


def _public_upi(u, entries=None):
    out = {"id": u["id"], "name": u.get("name", ""), "upi_id": u["upi_id"], "telegram_chat_id": u.get("telegram_chat_id", ""),
           "active": u.get("active", True), "has_scanner": bool(u.get("scanner_media_id")),
           "scanner_url": f"/media/upi/{u['id']}" if u.get("scanner_media_id") else "",
           "created_at": u.get("created_at", 0)}
    if entries is not None:
        mine = [e for e in entries if e.get("upi_id") == u["id"]]
        for name in ("today", "yesterday", "all"):
            lo, hi = utils.resolve_range(name)
            out[name] = sum(e["amount"] for e in mine if lo <= e["ts"] <= hi)
        out["success_count"] = len(mine)
    return out


def _get_upi(upi_id):
    u = db.get(f"/upis/{upi_id}")
    if not u or u.get("deleted"):
        raise ApiError("UPI not found.", 404)
    return u


def _read_upi_form():
    f = request.form
    name = utils.clean_text(f.get("name"), "Name", 60)
    upi_id = utils.clean_text(f.get("upi_id"), "UPI ID", 80)
    if not UPI_RE.match(upi_id):
        raise ApiError("Enter a valid UPI ID like name@bank.")
    chat = utils.clean_chat_id(f.get("telegram_chat_id"), "Telegram chat ID")
    return name, upi_id, chat


@bp.get("/upis")
def list_upis():
    entries = stats.ledger()
    upis = [u for u in db.get_list("/upis") if not u.get("deleted")]
    upis.sort(key=lambda u: u.get("created_at", 0))
    return jsonify(items=[_public_upi(u, entries) for u in upis])


@bp.post("/upis")
def add_upi():
    name, upi_id, chat = _read_upi_form()
    file = request.files.get("scanner")
    if not file:
        raise ApiError("Upload the scanner (QR) image.")
    mid = media.save_scanner(file.read(), g.admin["username"])
    uid = utils.new_id("upi")
    db.set(f"/upis/{uid}", {"id": uid, "name": name, "upi_id": upi_id, "telegram_chat_id": chat,
                            "scanner_media_id": mid, "active": True, "created_at": utils.now(),
                            "created_by": g.admin["username"]})
    log_action("upi.created", f"{name} ({upi_id})", target=uid)
    return jsonify(_public_upi(db.get(f"/upis/{uid}"))), 201


@bp.post("/upis/<upi_id>")  # multipart edit (new scanner optional) -- POST so browsers can send files
def edit_upi(upi_id):
    u = _get_upi(upi_id)
    name, upi, chat = _read_upi_form()
    upd = {"name": name, "upi_id": upi, "telegram_chat_id": chat,
           "active": request.form.get("active", "1") in ("1", "true", "on")}
    file = request.files.get("scanner")
    if file and file.filename:
        upd["scanner_media_id"] = media.save_scanner(file.read(), g.admin["username"])
        upd["tg_file_id"] = ""  # force the bot to re-upload the new QR
    elif u.get("upi_id") != upi:
        upd["tg_file_id"] = ""
    db.update(f"/upis/{upi_id}", upd)
    log_action("upi.edited", f"{name} ({upi})", target=upi_id)
    return jsonify(_public_upi(db.get(f"/upis/{upi_id}")))


@bp.delete("/upis/<upi_id>")
def delete_upi(upi_id):
    u = _get_upi(upi_id)
    db.update(f"/upis/{upi_id}", {"deleted": True, "active": False, "deleted_at": utils.now(),
                                  "deleted_by": g.admin["username"]})
    log_action("upi.deleted", u["upi_id"], target=upi_id)
    return jsonify(ok=True)


@bp.get("/upis/<upi_id>/stats")
def upi_stats(upi_id):
    if upi_id != "all":
        _get_upi(upi_id)
    rng = request.args.get("range", "today")
    lo, hi = utils.resolve_range(rng, request.args.get("start"), request.args.get("end"))
    rows = [e for e in stats.entries_between(lo, hi) if e.get("upi_id") and (upi_id == "all" or e["upi_id"] == upi_id)]
    by_app, by_user = {}, {}
    for e in rows:
        a = by_app.setdefault(e["app_id"], {"app_id": e["app_id"], "app_name": e.get("app_name", ""), "amount": 0, "count": 0})
        a["amount"] += e["amount"]
        a["count"] += 1
        uu = e["user"]
        b = by_user.setdefault(uu["chat_id"], {**uu, "amount": 0, "count": 0})
        b["amount"] += e["amount"]
        b["count"] += 1
    return jsonify(total=round(sum(e["amount"] for e in rows), 2), count=len(rows),
                   by_app=sorted(by_app.values(), key=lambda x: -x["amount"]),
                   by_user=sorted(by_user.values(), key=lambda x: -x["amount"])[:50])


# ------------------------------------------------------------------ transactions
@bp.get("/txns")
def list_txns():
    payments.expire_due()
    status = request.args.get("status", "pending")
    upi = request.args.get("upi_id") or ""
    q = (request.args.get("q") or "").strip()
    if status not in ("pending", "verification", "failed", "success"):
        raise ApiError("Unknown status.")
    searching = bool(q)
    items = payments.search(q) if searching else db.query_equal("/txns", "status", status)
    if upi and upi != "all":
        items = [t for t in items if t.get("upi_record_id") == upi]
    if status == "pending" and not searching:
        items.sort(key=lambda t: t["expires_at"])
    else:
        items.sort(key=lambda t: t["created_at"], reverse=True)
    page, meta = utils.paginate(items, request.args.get("page"))
    counts = {s: len([t for t in db.query_equal("/txns", "status", s)
                      if not upi or upi == "all" or t.get("upi_record_id") == upi])
              for s in ("pending", "verification")}
    return jsonify(items=[payments.public_txn(t) for t in page], searching=searching, counts=counts, **meta)


@bp.post("/txns/<txn_id>/verify")
def verify(txn_id):
    lic = payments.verify_txn(txn_id.upper(), f"{g.admin['username']} (panel)", "panel")
    return jsonify(license_key=lic["key"])


@bp.post("/txns/<txn_id>/reject")
def reject(txn_id):
    reason = utils.clean_text(body().get("reason"), "Reason", 300)
    payments.fail_txn(txn_id.upper(), reason, f"{g.admin['username']} (panel)", "panel")
    return jsonify(ok=True)
