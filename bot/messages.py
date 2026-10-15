"""All user-facing bot texts and keyboards live here."""
import html

from core import utils


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=False)


MENU_HOME, MENU_APPS, MENU_KEYS, MENU_SUPPORT = "🏠 Home", "📱 Apps", "🔑 My License Key", "🛟 Customer Support"
MENU_KEYBOARD = {
    "keyboard": [[{"text": MENU_HOME}, {"text": MENU_APPS}], [{"text": MENU_KEYS}, {"text": MENU_SUPPORT}]],
    "resize_keyboard": True, "is_persistent": True, "input_field_placeholder": "Choose from the menu 👇",
}


def kb(*rows):
    return {"inline_keyboard": [[{"text": t, **({"url": d[4:]} if d.startswith("url:") else {"callback_data": d})}
                                 for t, d in row] for row in rows]}


def user_block(u):
    uname = f"@{esc(u.get('username'))}" if u.get("username") else "—"
    return (f"👤 <b>{esc(u.get('full_name'))}</b>\n🔗 Username: {uname}\n"
            f"🆔 Chat ID: <code>{esc(u.get('chat_id'))}</code>")


def render_welcome(template, fmt, user):
    """Fill {first_name} {full_name} {username} {chat_id}. Returns (text, parse_mode)."""
    html_mode = fmt != "text"
    clean = (lambda v: esc(v)) if html_mode else (lambda v: str(v or ""))
    text = template
    for k in ("first_name", "full_name", "username", "chat_id"):
        text = text.replace("{" + k + "}", clean(user.get(k, "")))
    return text, ("HTML" if html_mode else None)


def mmss(seconds):
    m, s = divmod(max(0, int(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def payment_caption(txn, note=""):
    rem = txn["expires_at"] - utils.now()
    return (
        "💳 <b>Payment Request</b>\n\n"
        f"📱 App: <b>{esc(txn['app_name'])}</b>\n"
        f"📦 Plan: <b>{esc(txn['plan_label'])}</b>\n"
        f"💰 Amount: <b>{esc(utils.inr(txn['amount']))}</b>\n\n"
        f"🏦 UPI ID: <code>{esc(txn['upi_id'])}</code>\n"
        f"🧾 Txn ID: <code>{esc(txn['id'])}</code>\n"
        f"⏳ Time remaining: <b>{mmss(rem)}</b> (valid till {utils.fmt_time(txn['expires_at'])})\n\n"
        f"Scan the QR or pay exactly <b>{esc(utils.inr(txn['amount']))}</b> to the UPI ID above, then tap "
        "<b>I have Paid</b>." + (f"\n\n{note}" if note else "")
    )


def card_markup(txn_id):
    return kb([("✅ I have Paid", f"paid:{txn_id}"), ("❌ Cancel", f"cancel:{txn_id}")],
              [("🔄 Refresh timer", f"ref:{txn_id}")])


def admin_created_text(txn):
    return ("🆕 <b>New payment created</b>\n\n"
            f"📱 App: <b>{esc(txn['app_name'])}</b> — {esc(txn['plan_label'])}\n"
            f"💰 Amount: <b>{esc(utils.inr(txn['amount']))}</b>\n"
            f"🏦 UPI: <code>{esc(txn['upi_id'])}</code>\n"
            f"🧾 Txn: <code>{esc(txn['id'])}</code>\n"
            f"⏳ Expires: {utils.fmt_time(txn['expires_at'])}\n\n" + user_block(txn["user"]))


def admin_proof_caption(txn, status_line=""):
    return ("📸 <b>Payment proof submitted</b>\n\n"
            f"🧾 Txn: <code>{esc(txn['id'])}</code>\n"
            f"🔢 UTR: <code>{esc(txn.get('utr', ''))}</code>\n"
            f"📱 App: <b>{esc(txn['app_name'])}</b> — {esc(txn['plan_label'])}\n"
            f"💰 Amount: <b>{esc(utils.inr(txn['amount']))}</b>\n"
            f"🏦 UPI: <code>{esc(txn['upi_id'])}</code>\n\n" + user_block(txn["user"]) +
            (f"\n\n{status_line}" if status_line else ""))


def admin_buttons(txn_id):
    return kb([("✅ Verified", f"av:{txn_id}"), ("❌ Fail", f"af:{txn_id}")])
