# ⚡ GZ Panel — admin panel + Telegram license-selling bot

Python (Flask) · HTML/CSS/JS · Firebase Realtime Database · built for **Render free web service**.

* **Admin panel** — dashboard, apps, license keys, users, earnings, UPI IDs, plans, logs, settings
* **Telegram bot** — force-join → pick app → pick plan → pay by UPI (QR) → upload screenshot + UTR → manual verify → auto 18-char license key
* **License API** — your apps call `POST /api/v1/<app_id>/verify` (see [API_DOCS.md](API_DOCS.md))

## 1. Firebase (5 min)
1. [console.firebase.google.com](https://console.firebase.google.com) → create project → **Build → Realtime Database → Create database** (any region).
2. **Rules** tab → paste the content of `database.rules.json` → Publish. (Clients are locked out; only your server, via the service account, can read/write. The indexes make queries fast.)
3. **Project settings → Service accounts → Generate new private key** → a JSON file downloads.
4. Copy the database URL (looks like `https://xxxx-default-rtdb.firebaseio.com`).

## 2. Telegram bot
1. [@BotFather](https://t.me/BotFather) → `/newbot` → copy the **token**.
2. Add the bot as **administrator** in each channel you want to force-join (needed to check membership).
3. Get your own chat id from [@userinfobot](https://t.me/userinfobot) — this is your **Admin Chat ID** (also message your bot once with /start so it can write to you). Each UPI owner must also press /start on the bot once.

## 3. Deploy on Render
1. Push this folder to a GitHub repo (the `.gitignore` keeps secrets out).
2. Render → **New → Web Service** → pick the repo (or use *Blueprint* with `render.yaml`).
   * Build: `pip install -r requirements.txt`
   * Start: `gunicorn wsgi:app --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:$PORT`
   * Health check path: `/healthz`
3. **Environment variables**

| Variable | Value |
|---|---|
| `SECRET_KEY` | long random string (Render can generate it) |
| `FIREBASE_DB_URL` | your database URL |
| `FIREBASE_CREDENTIALS_JSON` | the whole service-account JSON pasted on one line |
| `BOT_TOKEN` | token from BotFather |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | first admin (password: 10+ chars, letters + numbers). If you omit the password, a random one is printed once in the Render logs |
| `ADMIN_CHAT_ID` | optional; can be set in Settings → Telegram bot |
| `TIMEZONE` | default `Asia/Kolkata` (used for today / yesterday) |

4. Open `https://YOUR-SERVICE.onrender.com` → sign in → **Settings → Telegram bot**: set Admin Chat ID, support username, welcome message → Save → **Send test**.
   The webhook registers itself on every start (button *Re-register webhook* if ever needed).
5. **Home → New app**, **Plans → Add plan**, **UPI ID → Add UPI** (upload the QR), **Settings → Force join** (add channels). Open your bot and try a purchase.

### ⚠️ Render free tier sleeps
Free services sleep after ~15 min without traffic; the first request then takes ~30–60 s. Telegram retries
webhooks, so the bot recovers, but to keep it snappy ping `https://YOUR-SERVICE.onrender.com/healthz` every
5 minutes with a free monitor (UptimeRobot / cron-job.org). Payment expiry (10 min) is also swept while the
service is awake, and re-checked whenever the panel or a user touches a transaction.

## How the important rules work
* **Payments**: 10-minute window · max **5 requests / user / day** · a user with a txn *in verification* can't start another · failed txns can be retried.
* **UPI selection**: 1 UPI → used; 2+ active UPIs → **round-robin**.
* **Notifications**: UPI owner chat **and** admin chat get new-user, payment-created (with profile photo), proof (screenshot + UTR as a reply to the payment message, **Verified / Fail** buttons — Fail asks for a reason) and result messages. The panel can verify/reject too; both paths use the same code and can't double-process.
* **Fraud protection**: SHA-256 hash + Telegram `file_unique_id` of every screenshot, and every UTR, are stored; reusing one that belongs to a txn in verification/success is refused. (Failed txns release theirs, so an honest retry works.)
* **Images**: screenshots stay on Telegram's servers; the DB stores the file id + an admin-only proxy URL (`/media/tg/…`), so the bot token never reaches the browser. UPI scanners are stored (re-encoded) in the database.
* **Soft delete**: nothing is ever removed from the database — "delete" sets `deleted: true`.
* **Active users** = distinct license keys that made a successful API call in the period.
* **Logs**: every panel and Telegram-admin action is stored with the admin name **and IP address**; the UI never shows the IP.
* **Admins**: the first admin has full control. Settings → Admins creates **view-only** admins (can read everything, cannot change anything). Passwords are PBKDF2-hashed; changing/resetting a password ends that user's other sessions.

## Security checklist (what's built in)
HTTPS-only secure cookies (`__Host-` prefix) · HttpOnly + SameSite · CSRF token on every change · strict CSP (no inline JS/CSS) · clickjacking/MIME/referrer headers · login rate limit + persistent account lockout (5 fails → 15 min) · constant-time failure path · server-side role enforcement · input validation + escaping everywhere · image uploads re-encoded · webhook protected by secret path **and** Telegram secret header · public API: API key + per-IP / per-license rate limits + guess lockout · Firebase locked by rules.

## Local development
```bash
pip install -r requirements.txt
APP_ENV=development USE_MEMORY_DB=1 ADMIN_PASSWORD=ChangeMe12345 python app.py   # http://127.0.0.1:5000
python tests/smoke_test.py      # end-to-end backend + bot flow (no network needed)
```
`USE_MEMORY_DB=1` is only for tests — data vanishes on restart. Production uses Firebase.

## Project layout
```
app.py  wsgi.py  config.py        app factory, entrypoint, env config
core/        db (Firebase + memory), security, audit, utils, cache
services/    licenses, payments, stats, settings, notify, media, users, earnings
routes/      admin JSON API, public license API, webhook, media proxy, pages
bot/         tg client, handlers (flow), messages (texts), antispam
templates/   login.html, app.html      static/ css + ES-module JS views
tests/       smoke_test.py (backend/bot), ui_check.py (browser)
```
