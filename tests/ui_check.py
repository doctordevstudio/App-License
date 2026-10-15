"""Browser check: seeds data (via smoke_test), serves the app, logs in with Chromium, visits every page,
reports console / CSP errors and saves screenshots.   python tests/ui_check.py <out_dir>"""
import os
import runpy
import sys
import threading
import time

here = os.path.dirname(os.path.abspath(__file__))
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ui_shots"
os.makedirs(out, exist_ok=True)
ns = runpy.run_path(os.path.join(here, "smoke_test.py"))
app, db = ns["app"], ns["db"]

# extra rows so the UPI page has pending / verification items to render
from core import utils  # noqa: E402

now = utils.now()
upi = db.get_list("/upis")[0]
base = {"user_id": "111", "amount": 199, "app_id": db.get_list("/apps")[0]["id"], "app_name": "Super Tool", "plan_id": "p",
        "plan_label": "30 Days", "validity_value": 30, "validity_unit": "days", "upi_record_id": upi["id"], "upi_id": upi["upi_id"],
        "created_at": now, "expires_at": now + 540,
        "user": {"chat_id": "111", "full_name": "Rahul K", "username": "u111", "profile_image_url": "", "profile_file_id": ""}}
db.set("/txns/TXNPENDING01", {**base, "id": "TXNPENDING01", "status": "pending"})
db.set("/txns/TXNVERIF0001", {**base, "id": "TXNVERIF0001", "status": "verification", "utr": "999988887777", "created_at": now - 60})

from werkzeug.serving import make_server  # noqa: E402

srv = make_server("127.0.0.1", 5055, app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)

from playwright.sync_api import sync_playwright  # noqa: E402

problems = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for label, vp in (("desktop", {"width": 1280, "height": 900}), ("mobile", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=vp)
        page = ctx.new_page()
        page.on("console", lambda m, l=label: problems.append(f"[{l}] console.{m.type}: {m.text}") if m.type in ("error", "warning") and "fonts.g" not in m.text and "ERR_" not in m.text and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e, l=label: problems.append(f"[{l}] pageerror: {e}"))
        page.goto("http://127.0.0.1:5055/login")
        page.screenshot(path=f"{out}/{label}-login.png")
        page.fill("input[name=username]", "boss")
        page.fill("input[name=password]", "TestPassw0rd123")
        page.click("button[type=submit]")
        page.wait_for_selector("#meName:not(:has-text('…'))", timeout=8000)
        for name, hash_ in (("home", "#/"), ("users", "#/users"), ("earning", "#/earning"), ("upi", "#/upi"),
                            ("plans", "#/plans"), ("logs", "#/logs"), ("settings", "#/settings")):
            page.evaluate(f"location.hash = '{hash_}'")
            page.wait_for_timeout(900)
            page.screenshot(path=f"{out}/{label}-{name}.png", full_page=False)
        # app page + modals
        page.evaluate("location.hash = '#/'")
        page.wait_for_timeout(700)
        page.click(".app-card")
        page.wait_for_timeout(900)
        page.screenshot(path=f"{out}/{label}-app.png")
        page.click("#newLic")
        page.wait_for_timeout(400)
        page.screenshot(path=f"{out}/{label}-modal.png")
        page.keyboard.press("Escape")
        page.fill("#globalSearch", "Manual")
        page.press("#globalSearch", "Enter")
        page.wait_for_timeout(1200)
        page.screenshot(path=f"{out}/{label}-search.png")
        ctx.close()
    b.close()
srv.shutdown()
print("\nUI problems:" if problems else "\nNo console/CSP errors", *problems, sep="\n")
