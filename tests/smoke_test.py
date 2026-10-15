"""End-to-end smoke test: panel API + public API + full bot purchase flow (in-memory DB, fake Telegram).

Run:  python tests/smoke_test.py
"""
import io
import os
import sys

os.environ.update(APP_ENV="development", USE_MEMORY_DB="1", ADMIN_USERNAME="boss", ADMIN_PASSWORD="TestPassw0rd123",
                  BOT_TOKEN="123:fake", ADMIN_CHAT_ID="999", SECRET_KEY="test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from app import create_app  # noqa: E402
from bot import handlers  # noqa: E402
from bot.tg import tg  # noqa: E402
from core import db  # noqa: E402

# ----------------------------------------------------------------- fake telegram
CALLS, _mid = [], [100]


def fake_call(method, payload=None, files=None, timeout=20):
    CALLS.append((method, payload or {}))
    _mid[0] += 1
    if method in ("sendMessage", "editMessageText", "editMessageCaption"):
        return {"message_id": _mid[0]}
    if method == "sendPhoto":
        return {"message_id": _mid[0], "photo": [{"file_id": "CACHEDQRFILEID01"}]}
    if method == "getChatMember":
        return {"status": "member" if (payload or {}).get("chat_id") != "-100777" else "left"}
    if method == "getUserProfilePhotos":
        return {"total_count": 1, "photos": [[{"file_id": "PROFILEFILE000002"}, {"file_id": "PROFILEFILE000002"}]]}
    return True


tg.call = fake_call
tg.download = lambda fid: (("img-" + fid).encode() * 50, "jpg")


def sent(method="sendMessage", contains=""):
    return [p for m, p in CALLS if m == method and contains in str(p)]


def ok(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        raise SystemExit(f"FAILED: {label}")


app = create_app(background=False)
c = app.test_client()


def j(r):
    return r.get_json()


# ----------------------------------------------------------------- auth + security
ok(c.get("/admin/api/me").status_code == 401, "API requires login")
page = c.get("/login")
csrf = page.get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
r = c.post("/login", data={"username": "boss", "password": "wrong", "csrf_token": csrf})
ok(r.status_code == 401, "wrong password rejected")
r = c.post("/login", data={"username": "boss", "password": "TestPassw0rd123", "csrf_token": "bad"})
ok(r.status_code == 400, "login without valid CSRF rejected")
r = c.post("/login", data={"username": "boss", "password": "TestPassw0rd123", "csrf_token": csrf})
ok(r.status_code == 302, "admin login works")
me = j(c.get("/admin/api/me"))
H = {"X-CSRF-Token": me["csrf"]}
ok(me["role"] == "admin", "role is admin")
ok(c.post("/admin/api/apps", json={"name": "x"}).status_code == 403, "mutation without CSRF header rejected")
hdrs = c.get("/").headers
ok("default-src 'self'" in hdrs["Content-Security-Policy"] and hdrs["X-Frame-Options"] == "DENY", "security headers set")

# ----------------------------------------------------------------- setup data
r = c.post("/admin/api/apps", json={"name": "Super Tool", "image_url": "https://example.com/i.png"}, headers=H)
ok(r.status_code == 201, "app created")
app_row = j(r)
APP = app_row["id"]
ok(app_row["endpoint"].endswith(f"/api/v1/{APP}/verify"), "endpoint auto-generated")
ok(c.post("/admin/api/apps", json={"name": "bad", "image_url": "javascript:alert(1)"}, headers=H).status_code == 400,
   "javascript: image url rejected")
ok(j(c.post("/admin/api/plans", json={"app_id": APP, "amount": 199, "validity_value": 30, "validity_unit": "days"},
            headers=H))["ok"], "plan 30 days created")
c.post("/admin/api/plans", json={"app_id": APP, "amount": 49, "validity_value": 12, "validity_unit": "hours"}, headers=H)
_b = io.BytesIO()
Image.new("RGB", (300, 300), "white").save(_b, "PNG")
PNG = _b.getvalue()
r = c.post("/admin/api/upis", data={"name": "Shop A", "upi_id": "shop.a@okbank", "telegram_chat_id": "555",
                                    "scanner": (io.BytesIO(PNG), "qr.png")}, headers=H, content_type="multipart/form-data")
ok(r.status_code == 201, "UPI added with scanner")
UPI1 = j(r)["id"]
ok(c.post("/admin/api/upis", data={"name": "x", "upi_id": "bad", "telegram_chat_id": "1"}, headers=H,
          content_type="multipart/form-data").status_code == 400, "bad UPI id rejected")
r = c.post("/admin/api/upis", data={"name": "Shop B", "upi_id": "shopb@ybl", "telegram_chat_id": "556",
                                    "scanner": (io.BytesIO(PNG), "qr.png")}, headers=H,
           content_type="multipart/form-data")
UPI2 = j(r)["id"]
ok(c.put("/admin/api/settings/bot", json={"welcome_message": "<b>Hi {first_name}</b>", "welcome_format": "html",
                                          "support_username": "my_support", "admin_chat_id": "999"},
         headers=H).status_code == 200, "bot settings saved")

# ----------------------------------------------------------------- manual license + public API
r = c.post(f"/admin/api/apps/{APP}/licenses", json={
    "full_name": "Manual Mia", "chat_id": "4242", "username": "@mia", "profile_image_url": "",
    "amount": 100, "validity_value": 2, "validity_unit": "days"}, headers=H)
ok(r.status_code == 201, "manual license created")
KEY = j(r)["key"]
ok(len(KEY) == 18, "license key is 18 chars")
apikey = j(c.get(f"/admin/api/apps/{APP}"))["api_key"]
API = f"/api/v1/{APP}/verify"
pub = lambda body, key=apikey: c.post(API, json=body, headers={"X-API-Key": key})  # noqa: E731
ok(pub({"license_key": KEY, "device_id": "device-AAAA-1111"}).status_code == 200, "verify ok + device bound")
ok(pub({"license_key": KEY, "device_id": "device-BBBB-2222"}).get_json()["code"] == "DEVICE_MISMATCH",
   "second device refused")
ok(pub({"license_key": KEY, "device_id": "device-AAAA-1111"}, key="nope").status_code == 401, "bad API key refused")
ok(pub({"license_key": "A" * 18, "device_id": "device-AAAA-1111"}).get_json()["code"] == "LICENSE_NOT_FOUND",
   "unknown key refused")
ok(c.post(f"/admin/api/licenses/{KEY}/reset-device", headers=H).status_code == 200, "device reset")
ok(pub({"license_key": KEY, "device_id": "device-BBBB-2222"}).status_code == 200, "new device binds after reset")
c.post(f"/admin/api/licenses/{KEY}/revoke", json={"reason": "chargeback"}, headers=H)
ok(pub({"license_key": KEY, "device_id": "device-BBBB-2222"}).get_json()["code"] == "LICENSE_REVOKED", "revoked refused")
c.post(f"/admin/api/licenses/{KEY}/renew", json={"validity_value": 5, "validity_unit": "days"}, headers=H)
ok(pub({"license_key": KEY, "device_id": "device-BBBB-2222"}).status_code == 200, "renew reactivates")
c.post("/admin/api/maintenance", json={"app_id": "all", "message": "Back soon", "status_code": 301, "enabled": True},
       headers=H)
r = pub({"license_key": KEY, "device_id": "device-BBBB-2222"})
ok(r.status_code == 301 and r.get_json()["message"] == "Back soon", "maintenance returns configured code + message")
rule = db.get_list("/maintenance")[0]
c.patch(f"/admin/api/maintenance/{rule['id']}", json={"enabled": False}, headers=H)
from services.settings import cache  # noqa: E402
ok(pub({"license_key": KEY, "device_id": "device-BBBB-2222"}).status_code == 200, "maintenance off -> normal")

# rate limiting + bad-guess lockout
from core.security import limiter  # noqa: E402
codes = [pub({"license_key": f"{i:018d}", "device_id": "device-AAAA-1111"}).status_code for i in range(15)]
ok(429 in codes, "guessing keys triggers 429 / IP block")
limiter._h.clear(); limiter._blocks.clear()

# ----------------------------------------------------------------- bot purchase flow
U, U2 = 111, 222


def msg(uid, text=None, photo=None, name="Rahul"):
    m = {"message_id": 1, "from": {"id": uid, "first_name": name, "last_name": "K", "username": f"u{uid}"},
         "chat": {"id": uid, "type": "private"}}
    if text is not None:
        m["text"] = text
    if photo:
        m["photo"] = [{"file_id": photo, "file_unique_id": "uniq" + photo, "file_size": 100}]
    return {"update_id": id(m) % 10**9, "message": m}


def cb(uid, data, chat=None, mid=7):
    return {"update_id": id(data) % 10**9, "callback_query": {
        "id": "cb1", "from": {"id": uid, "first_name": "Rahul", "username": f"u{uid}"}, "data": data,
        "message": {"message_id": mid, "chat": {"id": chat or uid, "type": "private"}}}}


from bot.antispam import spam  # noqa: E402

_orig_process = handlers.process_update


def _process(u):
    spam._s.clear()  # the flood limiter has its own dedicated test below
    return _orig_process(u)


handlers.process_update = _process

# force-join channel present -> user must verify
ch = {"id": "ch_test1", "chat_id": "-100777", "title": "Test Channel", "invite_link": "https://t.me/test", "created_at": 1}
db.set("/channels/ch_test1", ch)
handlers.process_update(msg(U, "/start"))
ok(any("Join our channel" in str(p) for p in sent()), "force-join prompt shown")
ok(any(p.get("chat_id") == "999" for _, p in CALLS) or sent("sendPhoto", "New user"), "admin notified about new user")
ok(db.get("/users/111")["profile_file_id"] == "PROFILEFILE000002", "profile photo stored")
db.set("/channels/ch_test1", {**ch, "chat_id": "-100888"})  # bot says 'member' for this one
handlers._joined_until.clear()
CALLS.clear()
handlers.process_update(cb(U, "verify_join"))
ok(any("Hi Rahul" in str(p) for p in sent()), "welcome message rendered after verify")
ok(db.get("/channel_joins/ch_test1/111") is not None, "channel join recorded")
db.set("/channels/ch_test1", {**ch, "chat_id": "-100888", "deleted": True})

plan_id = [p for p in db.get_list("/plans") if p["amount"] == 199][0]["id"]
handlers.process_update(msg(U, "📱 Apps"))
ok(any("Choose an app" in str(p) for p in sent()), "apps menu works")
CALLS.clear()
handlers.process_update(cb(U, f"buy:{plan_id}"))
cards = sent("sendPhoto", "Payment Request")
ok(len(cards) == 1, "payment card with QR sent")
txn = db.query_equal("/txns", "status", "pending")[0]
TXN = txn["id"]
first_upi = txn["upi_record_id"]
ok(txn["expires_at"] - txn["created_at"] == 600, "txn valid for 10 minutes")
ok(any(p.get("chat_id") in ("555", "556") for p in sent("sendPhoto") + sent()) and
   any(p.get("chat_id") == "999" for m_, p in CALLS if m_ in ("sendMessage", "sendPhoto")), "UPI owner + admin notified")

handlers.process_update(cb(U, f"paid:{TXN}"))
ok(handlers.get_state(U).get("name") == "await_screenshot", "asks for screenshot")
handlers.process_update(msg(U, photo="SHOTAAAAAAAA1"))
ok(handlers.get_state(U).get("name") == "await_utr", "asks for UTR")
handlers.process_update(msg(U, "12345"))
ok(handlers.get_state(U).get("name") == "await_utr", "short UTR rejected")
CALLS.clear()
handlers.process_update(msg(U, "123456789012"))
ok(db.get(f"/txns/{TXN}/status") == "verification", "txn moved to verification")
ok(any("verification is pending" in str(p) for p in sent()), "user told to wait 2-3 hours")
ok(any("Verified" in str(p.get("reply_markup")) for p in sent("sendPhoto")), "admin gets proof + Verified/Fail buttons")
ok(any(p.get("reply_parameters") for p in sent("sendPhoto", "Payment proof")), "proof replies to the txn message")

# in-verification block + duplicate protection with a 2nd user
CALLS.clear()
handlers.process_update(cb(U, f"buy:{plan_id}"))
ok(any("in verification" in str(p) for p in sent("editMessageText")), "second purchase blocked while in verification")
spam._s.clear()
handlers.process_update(msg(U2, "/start", name="Priya"))
db.set("/channels/ch_test1", None) if False else None
handlers.process_update(cb(U2, f"buy:{plan_id}"))
t2 = [t for t in db.query_equal("/txns", "user_id", str(U2))][0]
handlers.process_update(cb(U2, f"paid:{t2['id']}"))
CALLS.clear()
handlers.process_update(msg(U2, photo="SHOTAAAAAAAA1"))  # same bytes+id as user 1
ok(any("already used" in str(p) for p in sent()), "reused screenshot rejected")
handlers.process_update(msg(U2, photo="SHOTBBBBBBBB2"))
CALLS.clear()
handlers.process_update(msg(U2, "123456789012"))
ok(any("UTR was already used" in str(p) for p in sent()), "reused UTR rejected")

# admin presses Verified in the UPI-owner chat
owner = db.get(f"/upis/{first_upi}")["telegram_chat_id"]
CALLS.clear()
handlers.process_update(cb(int(owner), f"av:{TXN}", chat=int(owner)))
tx = db.get(f"/txns/{TXN}")
ok(tx["status"] == "success" and len(tx["license_key"]) == 18, "admin verify -> success + 18 char key")
ok(any(tx["license_key"] in str(p) for p in sent()), "license key DM'd to user")
ok(db.get(f"/licenses/{tx['license_key']}")["chat_id"] == "111", "license stored for user")
CALLS.clear()
handlers.process_update(cb(int(owner), f"av:{TXN}", chat=int(owner)))
ok(any(p.get("show_alert") for m_, p in CALLS if m_ == "answerCallbackQuery"), "double verify refused")
handlers.process_update(cb(777, f"av:{t2['id']}", chat=777))
ok(db.get(f"/txns/{t2['id']}/status") != "success", "stranger cannot verify")

# fail flow with reason (admin chat)
spam._s.clear()
handlers.process_update(msg(U2, "123400000001"))
t2s = db.get(f"/txns/{t2['id']}")
db.update(f"/txns/{t2['id']}", {"status": "verification"})
handlers.process_update(cb(999, f"af:{t2['id']}", chat=999))
ok(handlers.get_state(999).get("name") == "fail_reason", "Fail asks for a reason")
CALLS.clear()
handlers.process_update(msg(999, "UTR not found in bank"))
ok(db.get(f"/txns/{t2['id']}/status") == "failed", "txn failed with reason")
ok(any("UTR not found in bank" in str(p) for p in sent()), "user told the reason")

# expiry
CALLS.clear()
spam._s.clear()
handlers.process_update(cb(U2, f"buy:{plan_id}"))
t3 = db.query_equal("/txns", "status", "pending")[0]
db.update(f"/txns/{t3['id']}", {"expires_at": 1})
from services import payments  # noqa: E402
ok(payments.expire_due() == 1 and db.get(f"/txns/{t3['id']}")["fail_type"] == "expired", "expired txns fail after 10 min")

# ----------------------------------------------------------------- antispam
CALLS.clear()
spam._s.clear()
for _ in range(12):
    _orig_process(msg(333, "hello"))
ok(len(sent("sendMessage", "Slow down")) == 1, "flooding user is muted and warned once")
spam._s.clear()

# ----------------------------------------------------------------- panel reads
s = j(c.get("/admin/api/dashboard"))
ok(s["total_apps"] == 1 and s["summary"]["earning"]["today"] == 299, "dashboard earnings (199 upi + 100 manual)")
ok(s["summary"]["active"]["all"] >= 1, "active users counted from API calls")
ok(j(c.get(f"/admin/api/stats?app_id={APP}&start=2026-01-01&end=2026-12-31"))["earning"]["custom"] == 299,
   "custom date range works")
ok(len(j(c.get("/admin/api/txns?status=success"))["items"]) == 1, "success tab")
ok(len(j(c.get("/admin/api/txns?status=failed"))["items"]) == 2, "failed tab (rejected + expired)")
ok(j(c.get(f"/admin/api/txns?q={TXN}"))["items"][0]["id"] == TXN, "search by txn id")
ok(j(c.get("/admin/api/txns?q=123456789012"))["items"][0]["id"] == TXN, "search by UTR")
us = j(c.get("/admin/api/users"))
ok(us["total"] >= 3 and us["per"] == 20, "users list paginated by 20")
ok(j(c.get("/admin/api/search?q=" + KEY))["items"][0]["chat_id"] == "4242", "global search by license key")
ok(j(c.get("/admin/api/search?q=%40u222"))["items"][0]["chat_id"] == "222", "global search by @username")
e = j(c.get("/admin/api/earnings?range=all"))
ok(e["amount_total"] == 299 and e["by_app"][0]["app_name"] == "Super Tool", "earnings by app with user details")
st = j(c.get(f"/admin/api/upis/{first_upi}/stats?range=all"))
ok(st["total"] == 199 and st["by_user"][0]["chat_id"] == "111", "UPI stats: amount, by app, by user")
pl = j(c.get("/admin/api/plans"))
ok(pl["sold_by_app"][0]["sold"] == 1, "plans sold counted per app")
lg = j(c.get("/admin/api/logs"))
ok(lg["items"] and all("ip" not in x for x in lg["items"]), "logs shown without IP address")
ok(any(x.get("ip") for x in db.get_list("/logs")), "IP address is stored in the database")
ok(any(x["action"] == "txn.verified" for x in lg["items"]), "logs capture telegram admin actions")

# soft delete keeps data
ok(c.delete(f"/admin/api/plans/{plan_id}", headers=H).status_code == 200, "plan soft-deleted")
ok(db.get(f"/plans/{plan_id}")["deleted"] is True, "plan row still in database")

# ----------------------------------------------------------------- view-only admin
r = c.post("/admin/api/admins", json={"username": "viewer1", "password": "ViewerPass123"}, headers=H)
ok(r.status_code == 201, "view-only admin created")
v = app.test_client()
tok = v.get("/login").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
ok(v.post("/login", data={"username": "viewer1", "password": "ViewerPass123", "csrf_token": tok}).status_code == 302,
   "viewer logs in")
vh = {"X-CSRF-Token": j(v.get("/admin/api/me"))["csrf"]}
ok(v.get("/admin/api/dashboard").status_code == 200, "viewer can read")
ok(v.post("/admin/api/apps", json={"name": "hack"}, headers=vh).status_code == 403, "viewer cannot write")
ok(v.get("/admin/api/admins").status_code == 403, "viewer cannot list admins")

# login lockout
for _ in range(5):
    t = app.test_client()
    tk = t.get("/login").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
    t.post("/login", data={"username": "viewer1", "password": "bad", "csrf_token": tk})
    limiter._h.clear()
t = app.test_client()
tk = t.get("/login").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
r = t.post("/login", data={"username": "viewer1", "password": "ViewerPass123", "csrf_token": tk})
ok(r.status_code == 429, "account locked after repeated failures")

# webhook protection
from config import Config  # noqa: E402
ok(c.post("/telegram/wrong", json={}).status_code == 404, "webhook wrong path -> 404")
ok(c.post(f"/telegram/{Config.WEBHOOK_PATH_SECRET}", json={"update_id": 1}).status_code == 403,
   "webhook without secret header -> 403")
ok(c.post(f"/telegram/{Config.WEBHOOK_PATH_SECRET}", json={"update_id": 424242},
          headers={"X-Telegram-Bot-Api-Secret-Token": Config.WEBHOOK_HEADER_SECRET}).status_code == 200,
   "webhook with correct secrets accepted")
print("\nALL CHECKS PASSED")
