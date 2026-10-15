from core import db, utils


def full_name_of(tg_user):
    name = " ".join(x for x in (tg_user.get("first_name"), tg_user.get("last_name")) if x).strip()
    return name or tg_user.get("username") or str(tg_user.get("id"))


def upsert_user(chat_id, full_name=None, username=None, profile_image_url=None,
                profile_file_id=None, source="bot"):
    """Create or refresh a user. Only non-empty values overwrite existing ones."""
    chat_id = str(chat_id)
    u = db.get(f"/users/{chat_id}")
    t = utils.now()
    if not u:
        u = {"chat_id": chat_id, "id": chat_id, "full_name": full_name or chat_id, "username": username or "",
             "profile_image_url": profile_image_url or "", "profile_file_id": profile_file_id or "",
             "joined_at": t, "last_seen": t, "source": source}
        db.set(f"/users/{chat_id}", u)
        return u, True
    upd = {"last_seen": t}
    if full_name and full_name != u.get("full_name"):
        upd["full_name"] = full_name
    if username is not None and username != u.get("username") and (username or source == "bot"):
        upd["username"] = username
    if profile_image_url and not u.get("profile_image_url"):
        upd["profile_image_url"] = profile_image_url
    if profile_file_id:
        upd["profile_file_id"] = profile_file_id
        upd["profile_image_url"] = profile_image_url or u.get("profile_image_url", "")
    db.update(f"/users/{chat_id}", upd)
    u.update(upd)
    return u, False
