from flask import g, jsonify, request

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from services import licenses as lic_svc
from services import stats
from . import body, bp


def public_app(a, with_key=False):
    base = request.url_root.rstrip("/")
    out = {"id": a["id"], "name": a["name"], "image_url": a.get("image_url", ""),
           "endpoint": f"{base}/api/v1/{a['id']}/verify", "heartbeat_endpoint": f"{base}/api/v1/{a['id']}/heartbeat",
           "created_at": a.get("created_at", 0)}
    if with_key:
        out["api_key"] = a["api_key"]
    return out


def get_app_or_404(app_id):
    a = db.get(f"/apps/{app_id}")
    if not a or a.get("deleted"):
        raise ApiError("App not found.", 404)
    return a


@bp.get("/dashboard")
def dashboard():
    start, end = request.args.get("start"), request.args.get("end")
    summ = stats.summary(None, start, end)
    apps = stats.live_apps()
    per = stats.today_per_app()
    counts = {}
    for l in lic_svc.all_licenses():
        counts[l["app_id"]] = counts.get(l["app_id"], 0) + 1
    cards = []
    for a in sorted(apps, key=lambda x: x.get("created_at", 0), reverse=True):
        p = public_app(a)
        p.update(today_active=per.get(a["id"], {}).get("active", 0),
                 today_earning=per.get(a["id"], {}).get("earning", 0), licenses=counts.get(a["id"], 0))
        cards.append(p)
    return jsonify(total_apps=len(apps), summary=summ, apps=cards)


@bp.get("/stats")
def app_stats():
    app_id = request.args.get("app_id")
    if app_id:
        get_app_or_404(app_id)
    return jsonify(stats.summary(app_id or None, request.args.get("start"), request.args.get("end")))


@bp.get("/apps")
def list_apps():
    return jsonify(apps=[public_app(a) for a in stats.live_apps()])


@bp.post("/apps")
def create_app():
    d = body()
    name = utils.clean_text(d.get("name"), "App name", 60)
    image = utils.clean_url(d.get("image_url"), "Image URL")
    aid = utils.new_id("app").replace("app_", "app_", 1)
    a = {"id": aid, "name": name, "image_url": image, "api_key": utils.gen_api_key(),
         "created_at": utils.now(), "created_by": g.admin["username"]}
    db.set(f"/apps/{aid}", a)
    log_action("app.created", name, target=aid)
    return jsonify(public_app(a, with_key=True)), 201


@bp.get("/apps/<app_id>")
def get_app(app_id):
    a = get_app_or_404(app_id)
    return jsonify(public_app(a, with_key=g.admin["role"] == "admin"))


@bp.patch("/apps/<app_id>")
def edit_app(app_id):
    a = get_app_or_404(app_id)
    d = body()
    upd = {}
    if "name" in d:
        upd["name"] = utils.clean_text(d["name"], "App name", 60)
    if "image_url" in d:
        upd["image_url"] = utils.clean_url(d["image_url"], "Image URL")
    if upd:
        db.update(f"/apps/{app_id}", upd)
        log_action("app.edited", ", ".join(upd), target=app_id)
    a.update(upd)
    return jsonify(public_app(a))


@bp.delete("/apps/<app_id>")
def delete_app(app_id):
    a = get_app_or_404(app_id)
    db.update(f"/apps/{app_id}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": g.admin["username"]})
    log_action("app.deleted", a["name"], target=app_id)
    return jsonify(ok=True)


@bp.post("/apps/<app_id>/regenerate-key")
def regen_key(app_id):
    get_app_or_404(app_id)
    key = utils.gen_api_key()
    db.update(f"/apps/{app_id}", {"api_key": key, "key_rotated_at": utils.now()})
    log_action("app.key_regenerated", "", target=app_id)
    return jsonify(api_key=key)
