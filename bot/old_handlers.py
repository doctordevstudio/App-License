"""Telegram update handling: welcome -> force join -> apps -> plans -> pay -> proof -> license."""
import hashlib
import logging
import re
import time

from bot import messages as m
from bot.antispam import spam
from bot.tg import TelegramError, tg
from core import db, utils
from core.errors import ApiError
from services import licenses as lic_svc
from services import notify, payments, settings, users

log = logging.getLogger("bot")
UTR_RE = re.compile(r"^\d{12}$")
_joined_until = {}  # user_id -> epoch until which the force-join check is cached


# ------------------------------------------------------------------ small helpers
def send(chat_id, text, markup=None, mode="HTML"):
    try:
        return tg.send_message(chat_id, text, markup, parse_mode=mode)
    except TelegramError as e:
        log.warning("send to %s failed: %s", chat_id, e)
        return None


def show(cq_or_chat, text, markup=None):
    """Edit the tapped message when possible (keeps chats tidy), otherwise send a new one."""
    if isinstance(cq_or_chat, dict) and cq_or_chat.get("message"):
        msg = cq_or_chat["message"]
        if not msg.get("photo"):
            try:
                return tg.edit_text(msg["chat"]["id"], msg["message_id"], text, markup)
            except TelegramError as e:
                if "not modified" in e.description:
                    return None
        chat = msg["chat"]["id"]
    else:
        chat = cq_or_chat
    return send(chat, text, markup)


def get_state(uid):
    return db.get(f"/bot_state/{uid}") or {}


def set_state(uid, **kw):
    db.set(f"/bot_state/{uid}", {**kw, "ts": utils.now()})


def clear_state(uid):
    if get_state(uid):
        db.set(f"/bot_state/{uid}", None)


def touch_user(frm):
    uid = str(frm["id"])
    u, is_new = users.upsert_user(uid, users.full_name_of(frm), frm.get("username") or "", source="bot")
    if not u.get("profile_file_id") or utils.now() - int(u.get("profile_checked_at", 0)) > 7 * 86400:
        upd = {"profile_checked_at": utils.now()}
        try:
            fid = tg.profile_file_id(uid)
            if fid:
                upd.update(profile_file_id=fid, profile_image_url=f"/media/tg/{fid}")
        except TelegramError:
            pass
        db.update(f"/users/{uid}", upd)
        u.update(upd)
    if is_new:
        notify.new_user(u)
    return u


# ------------------------------------------------------------------ force join
def missing_channels(uid, use_cache=True):
    chans = settings.channels()
    if not chans:
        return []
    if use_cache and _joined_until.get(uid, 0) > time.time():
        return []
    missing = []
    for ch in chans:
        try:
            mem = tg.get_chat_member(ch["chat_id"], uid)
            st = mem.get("status")
            if st in ("creator", "administrator", "member") or (st == "restricted" and mem.get("is_member")):
                db.set_if_absent(f"/channel_joins/{ch['id']}/{uid}", utils.now())
            else:
                missing.append(ch)
        except TelegramError as e:  # bot not admin / bad channel: don't lock users out
            log.warning("membership check failed for %s: %s", ch.get("title"), e)
    if not missing:
        _joined_until[uid] = time.time() + 300
    return missing


def gate(uid, cq=None):
    miss = missing_channels(uid)
    if not miss:
        return True
    rows = [[(f"📢 {c['title'][:30]}", "url:" + c["invite_link"])] for c in miss if c.get("invite_link")]
    rows.append([("✅ I've joined — Verify", "verify_join")])
    text = ("🔒 <b>Join our channel to continue</b>\n\nPlease join "
            + ("all channels" if len(miss) > 1 else "the channel") + " below, then tap <b>Verify</b>.")
    if cq:
        show(cq, text, m.kb(*rows))
    else:
        send(uid, text, m.kb(*rows))
    return False


# ------------------------------------------------------------------ screens
def send_menu_hint(uid):
    send(uid, "👇 Use the menu below anytime to navigate.", m.MENU_KEYBOARD)


def send_home(uid, user):
    s = settings.bot_settings()
    first = (user.get("full_name") or "there").split()[0]
    text, mode = m.render_welcome(s["welcome_message"], s["welcome_format"], {
        "first_name": first, "full_name": user.get("full_name"), "username": user.get("username"), "chat_id": uid})
    markup = m.kb([("🛒 Purchase License Key", "apps")], [("🔑 My License Key", "keys"), ("🛟 Support", "support")])
    try:
        tg.send_message(uid, text, markup, parse_mode=mode)
    except TelegramError as e:
        if "parse" in e.description.lower():  # admin wrote invalid HTML -> still deliver
            send(uid, re.sub(r"<[^>]+>", "", text), markup, mode=None)
        else:
            log.warning("welcome failed: %s", e)


def show_apps(uid, cq=None):
    apps = [a for a in db.get_list("/apps") if not a.get("deleted")]
    with_plans = {p["app_id"] for p in db.get_list("/plans") if not p.get("deleted")}
    apps = sorted([a for a in apps if a["id"] in with_plans], key=lambda a: a.get("created_at", 0))
    if not apps:
        return show(cq or uid, "📭 No apps are available for purchase right now. Please check back soon.")
    rows = [[(f"📱 {a['name']}", f"app:{a['id']}")] for a in apps]
    show(cq or uid, "🛒 <b>Choose an app</b>\n\nPick the app you want a license key for:", m.kb(*rows))


def show_plans(uid, app_id, cq=None):
    app = db.get(f"/apps/{app_id}")
    plans = sorted([p for p in db.get_list("/plans") if p["app_id"] == app_id and not p.get("deleted")],
                   key=lambda p: p["amount"])
    if not app or app.get("deleted") or not plans:
        return show_apps(uid, cq)
    rows = [[(f"{utils.validity_label(p['validity_value'], p['validity_unit'])} — {utils.inr(p['amount'])}",
              f"plan:{p['id']}")] for p in plans]
    rows.append([("⬅️ Back", "apps")])
    show(cq or uid, f"📦 <b>{m.esc(app['name'])}</b>\n\nChoose a plan:", m.kb(*rows))


def show_keys(uid):
    rows = [l for l in db.query_equal("/licenses", "chat_id", str(uid)) if not l.get("deleted")]
    rows.sort(key=lambda l: l.get("created_at", 0), reverse=True)
    if not rows:
        return send(uid, "🔑 <b>My License Keys</b>\n\nYou don't have a license yet.",
                    m.kb([("🛒 Purchase License Key", "apps")]))
    icon = {"active": "🟢 Active", "expired": "🔴 Expired", "revoked": "⛔ Revoked"}
    parts = []
    for l in rows[:10]:
        st = lic_svc.status_of(l)
        parts.append(f"📱 <b>{m.esc(l.get('app_name'))}</b> — {m.esc(l.get('validity_label'))}\n"
                     f"<code>{l['key']}</code>\n{icon[st]} • until {utils.fmt_dt(l['expires_at'])}\n"
                     f"📲 Device: {'linked' if l.get('device_id') else 'not linked yet'}")
    send(uid, "🔑 <b>My License Keys</b>\n\n" + "\n\n".join(parts) + "\n\nTap a key to copy it.")


def show_support(uid):
    name = settings.bot_settings()["support_username"]
    if name:
        send(uid, "🛟 <b>Customer Support</b>\n\nNeed help? Our team is one tap away.",
             m.kb([("💬 Contact Support", f"url:https://t.me/{name}")]))
    else:
        send(uid, "🛟 Support is not configured yet. Please try again later.")


# ------------------------------------------------------------------ purchase
def confirm_plan(uid, plan_id, cq):
    p = db.get(f"/plans/{plan_id}")
    app = db.get(f"/apps/{p['app_id']}") if p else None
    if not p or p.get("deleted") or not app or app.get("deleted"):
        return show_apps(uid, cq)
    text = ("🛒 <b>Confirm purchase</b>\n\n"
            f"📱 App: <b>{m.esc(app['name'])}</b>\n"
            f"📦 Plan: <b>{utils.validity_label(p['validity_value'], p['validity_unit'])}</b>\n"
            f"💰 Price: <b>{m.esc(utils.inr(p['amount']))}</b>\n\nDo you want to purchase it?")
    show(cq, text, m.kb([("✅ Yes", f"buy:{plan_id}"), ("❌ No", f"app:{p['app_id']}")]))


def start_purchase(user, plan_id, cq):
    uid = user["chat_id"]
    status, txn = payments.create_txn(user, plan_id)
    if status == "in_verification":
        return show(cq, "⏳ <b>Your payment is in verification</b>\n\n"
                        f"🧾 Txn: <code>{txn['id']}</code>\nPlease wait 2-3 hours. You'll get your key here once approved.")
    if status == "daily_limit":
        return show(cq, f"🚫 You have reached the limit of <b>{payments.Config.MAX_TXN_PER_DAY}</b> payment requests "
                        "for today. Please try again tomorrow.")
    if status == "no_upi":
        notify.send_to(notify.recipients(), "⚠️ <b>No active UPI ID</b> — a user tried to buy but payments are off. "
                                            "Add/enable a UPI in the panel.")
        return show(cq, "⚠️ Payments are temporarily unavailable. Please try again later.")
    if status == "unavailable":
        return show(cq, "That plan is no longer available.", m.kb([("⬅️ Back to apps", "apps")]))
    if status == "existing":
        show(cq, "ℹ️ You already have an open payment request — here it is:")
        payments.send_card(txn)
        return
    show(cq, "✅ Payment request created. Pay within <b>10 minutes</b> 👇")
    payments.send_card(txn)
    payments.notify_created(txn)


def on_paid(uid, txn_id, cq):
    txn = payments.get_txn(txn_id)
    if not txn or txn["user_id"] != str(uid):
        return tg.answer_callback(cq["id"], "Transaction not found.", True)
    if txn["status"] == "verification":
        return tg.answer_callback(cq["id"], "Already submitted — it's in verification.", True)
    if txn["status"] != "pending" or txn["expires_at"] <= utils.now():
        payments.expire_due()
        return tg.answer_callback(cq["id"], "This payment request has expired. Please start again.", True)
    set_state(uid, name="await_screenshot", txn_id=txn_id)
    tg.answer_callback(cq["id"])
    send(uid, "📸 <b>Upload your payment screenshot</b>\n\nSend the screenshot of the successful payment as a photo.\n"
              "Send /cancel to go back.")


def on_screenshot(user, msg, st):
    uid = user["chat_id"]
    if msg.get("photo"):
        ph = msg["photo"][-1]
        fid, uniq, size = ph["file_id"], ph["file_unique_id"], ph.get("file_size", 0)
    elif (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
        d = msg["document"]
        fid, uniq, size = d["file_id"], d["file_unique_id"], d.get("file_size", 0)
    else:
        return send(uid, "Please send the payment <b>screenshot as a photo</b> (or /cancel).")
    if size and size > 8 * 1024 * 1024:
        return send(uid, "That image is too large (max 8 MB).")
    try:
        data, _ = tg.download(fid)
    except TelegramError:
        return send(uid, "Couldn't read that image. Please try again.")
    shot = {"file_id": fid, "unique_id": uniq, "sha256": hashlib.sha256(data).hexdigest()}
    if payments.find_duplicate(shot, "0" * 12, st["txn_id"]) == "image":
        return send(uid, "🚫 This screenshot was already used for another payment. Please send the correct one.")
    set_state(uid, name="await_utr", txn_id=st["txn_id"], shot=shot)
    send(uid, "✅ Screenshot received.\n\nNow send the <b>12-digit UTR / reference number</b> of the payment.")


def on_utr(user, text, st):
    uid = user["chat_id"]
    utr = re.sub(r"\s", "", text)
    if not UTR_RE.match(utr):
        return send(uid, "❌ The UTR must be exactly <b>12 digits</b>. Please send it again (or /cancel).")
    res = payments.submit_proof(st["txn_id"], uid, st["shot"], utr)
    if res == "image":
        clear_state(uid)
        return send(uid, "🚫 This screenshot was already used for another payment.")
    if res == "utr":
        return send(uid, "🚫 This UTR was already used for another payment. Check it and send the correct one.")
    clear_state(uid)
    if res == "expired":
        return send(uid, "⌛ This payment request expired. Please start a new purchase from <b>Apps</b>.")
    if res != "ok":
        return send(uid, "Something went wrong with this transaction. Please start again from <b>Apps</b>.")
    send(uid, "🕒 <b>Payment verification is pending</b>\n\n"
              f"🧾 Txn: <code>{st['txn_id']}</code>\n🔢 UTR: <code>{utr}</code>\n\n"
              "Please wait <b>2-3 hours</b> for manual verification. You'll receive your license key here.")


# ------------------------------------------------------------------ admin actions (Verified / Fail buttons)
def actor_name(frm):
    return (f"@{frm['username']}" if frm.get("username") else frm.get("first_name", "admin")) + f" (tg:{frm['id']})"


def on_admin_button(cq, action, txn_id):
    chat_id, frm = cq["message"]["chat"]["id"], cq["from"]
    txn = payments.get_txn(txn_id)
    if not txn or not payments.can_moderate(chat_id, txn):
        return tg.answer_callback(cq["id"], "You are not allowed to do this.", True)
    if txn["status"] != "verification":
        return tg.answer_callback(cq["id"], f"Already {txn['status']}.", True)
    if action == "av":
        try:
            payments.verify_txn(txn_id, actor_name(frm), "telegram")
            tg.answer_callback(cq["id"], "Verified ✅ — license sent to the user.")
        except ApiError as e:
            tg.answer_callback(cq["id"], e.message, True)
    else:
        set_state(frm["id"], name="fail_reason", txn_id=txn_id, chat_id=str(chat_id))
        tg.answer_callback(cq["id"])
        try:
            tg.send_message(chat_id, f"✍️ Enter the <b>reason</b> for failing txn <code>{txn_id}</code>:",
                            {"force_reply": True, "selective": True, "input_field_placeholder": "Reason…"},
                            reply_to=cq["message"]["message_id"])
        except TelegramError:
            pass


def finish_fail(msg, st):
    frm, chat = msg["from"], msg["chat"]["id"]
    reason = (msg.get("text") or "").strip()
    if reason == "/cancel":
        clear_state(frm["id"])
        return send(chat, "Okay, nothing changed.")
    try:
        payments.fail_txn(st["txn_id"], reason, actor_name(frm), "telegram")
        send(chat, f"❌ Txn <code>{st['txn_id']}</code> marked as failed. The user was notified.")
    except ApiError as e:
        send(chat, f"⚠️ {m.esc(e.message)}")
    clear_state(frm["id"])


# ------------------------------------------------------------------ dispatch
def process_update(u):
    if "message" in u:
        on_message(u["message"])
    elif "callback_query" in u:
        on_callback(u["callback_query"])


def on_message(msg):
    frm, chat = msg.get("from") or {}, msg["chat"]
    if not frm or frm.get("is_bot"):
        return
    uid = frm["id"]
    if chat["type"] != "private":  # groups: only the admin's "fail reason" reply is handled
        st = get_state(uid)
        if st.get("name") == "fail_reason" and msg.get("text") and str(chat["id"]) == st.get("chat_id"):
            finish_fail(msg, st)
        return
    allowed, warn, wait = spam.check(uid)
    if not allowed:
        if warn:
            send(uid, f"🚫 <b>Slow down!</b> Too many messages. Try again in {wait}s.")
        return
    user = touch_user(frm)
    text = (msg.get("text") or "").strip()
    st = get_state(uid)

    if text.startswith("/start"):
        clear_state(uid)
        if gate(uid):
            send_home(uid, user)
            send_menu_hint(uid)
        return
    if text == "/cancel":
        clear_state(uid)
        return send(uid, "Cancelled. Use the menu below to continue.", m.MENU_KEYBOARD)
    if text in (m.MENU_HOME, m.MENU_APPS, m.MENU_KEYS, m.MENU_SUPPORT, "/help", "/apps", "/keys", "/support"):
        clear_state(uid)
        if text in (m.MENU_SUPPORT, "/support"):
            return show_support(uid)
        if not gate(uid):
            return
        if text in (m.MENU_HOME, "/help"):
            return send_home(uid, user)
        if text in (m.MENU_APPS, "/apps"):
            return show_apps(uid)
        return show_keys(uid)

    if st.get("name") == "fail_reason":
        return finish_fail(msg, st)
    if st.get("name") == "await_screenshot":
        return on_screenshot(user, msg, st)
    if st.get("name") == "await_utr":
        if text:
            return on_utr(user, text, st)
        return send(uid, "Please send the 12-digit UTR as text (or /cancel).")
    send(uid, "Use the menu below 👇", m.MENU_KEYBOARD)


def on_callback(cq):
    frm, msg = cq["from"], cq.get("message")
    if not msg or frm.get("is_bot"):
        return
    uid, data = frm["id"], cq.get("data", "")
    allowed, warn, wait = spam.check(uid)
    if not allowed:
        return tg.answer_callback(cq["id"], f"Slow down ✋ ({wait}s)")
    if data.startswith(("av:", "af:")):
        return on_admin_button(cq, data[:2], data[3:])
    if msg["chat"]["type"] != "private":
        return tg.answer_callback(cq["id"])
    user = touch_user(frm)
    head, _, arg = data.partition(":")

    if head == "verify_join":
        _joined_until.pop(uid, None)
        miss = missing_channels(uid, use_cache=False)
        if miss:
            return tg.answer_callback(cq["id"], "You haven't joined: " + ", ".join(c["title"] for c in miss)[:150], True)
        tg.answer_callback(cq["id"], "Verified ✅")
        show(cq, "✅ <b>Verified!</b> Welcome aboard.")
        send_home(uid, user)
        return send_menu_hint(uid)
    if head == "support":
        tg.answer_callback(cq["id"])
        return show_support(uid)
    if not gate(uid, cq):
        return tg.answer_callback(cq["id"])
    tg.answer_callback(cq["id"]) if head not in ("paid", "cancel", "ref") else None

    if head == "apps":
        show_apps(uid, cq)
    elif head == "app":
        show_plans(uid, arg, cq)
    elif head == "plan":
        confirm_plan(uid, arg, cq)
    elif head == "buy":
        start_purchase(user, arg, cq)
    elif head == "keys":
        show_keys(uid)
    elif head == "paid":
        on_paid(uid, arg, cq)
    elif head == "cancel":
        txn = payments.get_txn(arg)
        if not txn or txn["user_id"] != str(uid):
            return tg.answer_callback(cq["id"], "Transaction not found.", True)
        try:
            payments.fail_txn(arg, "Cancelled by user", "user", "telegram", "cancelled", ("pending",))
            clear_state(uid)
            tg.answer_callback(cq["id"], "Payment cancelled.")
        except ApiError as e:
            tg.answer_callback(cq["id"], e.message, True)
    elif head == "ref":
        txn = payments.get_txn(arg)
        if not txn or txn["user_id"] != str(uid) or txn["status"] != "pending":
            return tg.answer_callback(cq["id"], "This request is no longer open.", True)
        tg.answer_callback(cq["id"], f"⏳ {m.mmss(txn['expires_at'] - utils.now())} remaining")
        try:
            tg.edit_caption(uid, msg["message_id"], m.payment_caption(txn), m.card_markup(arg))
        except TelegramError:
            pass
