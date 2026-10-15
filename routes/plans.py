from flask import g, jsonify

from core import db, utils
from core.audit import log_action
from core.errors import ApiError
from .apps import get_app_or_404
from . import body, bp


def _public(p, app_names, sold):
    return {"id": p["id"], "app_id": p["app_id"], "app_name": app_names.get(p["app_id"], ""),
            "amount": p["amount"], "validity_value": p["validity_value"], "validity_unit": p["validity_unit"],
            "label": utils.validity_label(p["validity_value"], p["validity_unit"]),
            "sold": sold.get(p["id"], 0), "created_at": p.get("created_at", 0)}


@bp.get("/plans")
def list_plans():
    apps = {a["id"]: a for a in db.get_list("/apps") if not a.get("deleted")}
    names = {k: v["name"] for k, v in apps.items()}
    sold_by_plan, sold_by_app = {}, {}
    for t in db.query_equal("/txns", "status", "success"):  # a "sale" = a verified UPI payment
        sold_by_plan[t["plan_id"]] = sold_by_plan.get(t["plan_id"], 0) + 1
        sold_by_app[t["app_id"]] = sold_by_app.get(t["app_id"], 0) + 1
    plans = [p for p in db.get_list("/plans") if not p.get("deleted") and p["app_id"] in apps]
    plans.sort(key=lambda p: (names.get(p["app_id"], ""), p["amount"]))
    return jsonify(
        items=[_public(p, names, sold_by_plan) for p in plans],
        sold_by_app=[{"app_id": k, "app_name": v, "sold": sold_by_app.get(k, 0)} for k, v in names.items()])


def _read(d):
    amount = utils.parse_amount(d.get("amount"))
    val, unit = utils.parse_validity(d.get("validity_value"), d.get("validity_unit"))
    return amount, val, unit


@bp.post("/plans")
def create_plan():
    d = body()
    app = get_app_or_404(str(d.get("app_id", "")))
    amount, val, unit = _read(d)
    pid = utils.new_id("plan")
    db.set(f"/plans/{pid}", {"id": pid, "app_id": app["id"], "amount": amount, "validity_value": val,
                             "validity_unit": unit, "created_at": utils.now(), "created_by": g.admin["username"]})
    log_action("plan.created", f"{app['name']} {utils.inr(amount)} / {utils.validity_label(val, unit)}", target=pid)
    return jsonify(ok=True, id=pid), 201


def _get_plan(pid):
    p = db.get(f"/plans/{pid}")
    if not p or p.get("deleted"):
        raise ApiError("Plan not found.", 404)
    return p


@bp.patch("/plans/<pid>")
def edit_plan(pid):
    p = _get_plan(pid)
    amount, val, unit = _read(body())
    db.update(f"/plans/{pid}", {"amount": amount, "validity_value": val, "validity_unit": unit,
                                "updated_at": utils.now()})
    log_action("plan.edited", f"{utils.inr(p['amount'])} -> {utils.inr(amount)} / {utils.validity_label(val, unit)}",
               target=pid)
    return jsonify(ok=True)


@bp.delete("/plans/<pid>")
def delete_plan(pid):
    _get_plan(pid)
    db.update(f"/plans/{pid}", {"deleted": True, "deleted_at": utils.now(), "deleted_by": g.admin["username"]})
    log_action("plan.deleted", "", target=pid)
    return jsonify(ok=True)
