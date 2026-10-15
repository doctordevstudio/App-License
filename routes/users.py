from flask import jsonify, request

from core import db, utils
from services import licenses as svc
from . import bp


def _user_rows(q=""):
    q = (q or "").strip().lower().lstrip("@")
    names = {a["id"]: a["name"] for a in db.get_list("/apps")}
    by_user = {}
    for l in svc.all_licenses():
        by_user.setdefault(str(l.get("chat_id")), []).append(l)
    users = {u["chat_id"]: u for u in db.get_list("/users") if not u.get("deleted") and u.get("chat_id")}
    for cid, ls in by_user.items():  # license holders that never opened the bot
        if cid not in users:
            l = ls[0]
            users[cid] = {"chat_id": cid, "full_name": l.get("full_name", ""), "username": l.get("username", ""),
                          "profile_image_url": l.get("profile_image_url", ""), "joined_at": l.get("created_at", 0),
                          "source": "manual"}
    rows = []
    for cid, u in users.items():
        ls = sorted(by_user.get(cid, []), key=lambda x: x.get("created_at", 0), reverse=True)
        if q:
            hay = [str(cid), (u.get("full_name") or "").lower(), (u.get("username") or "").lower()]
            hay += [l["key"].lower() for l in ls]
            if not any(q in h for h in hay):
                continue
        rows.append({"chat_id": cid, "full_name": u.get("full_name", ""), "username": u.get("username", ""),
                     "profile_image_url": u.get("profile_image_url", ""), "joined_at": u.get("joined_at", 0),
                     "last_seen": u.get("last_seen", 0), "source": u.get("source", "bot"),
                     "licenses": [svc.public(l, names) for l in ls]})
    rows.sort(key=lambda r: r["joined_at"], reverse=True)
    return rows


@bp.get("/users")
def list_users():
    page, meta = utils.paginate(_user_rows(request.args.get("q")), request.args.get("page"))
    return jsonify(items=page, **meta)


@bp.get("/search")
def global_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(items=[])
    return jsonify(items=_user_rows(q)[:20])
