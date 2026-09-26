# FPL Telegram Competition Platform — Backend (MVP)

Python (FastAPI) + PostgreSQL backend with a built-in secure admin panel,
implementing the core lifecycle: FPL manager verification, manual
Telebirr/CBE deposit verification, wallet ledger, gameweeks, and the
gameweek/prize state machine from the v2 spec.

## Stack
- FastAPI + SQLAlchemy 2.0 + PostgreSQL
- JWT auth for the API, session auth (separate secret) for the admin panel
- `sqladmin` for the admin dashboard (Users, Deposits, Withdrawals,
  Gameweeks, Prizes, Disputes, Audit Logs)
- bcrypt password hashing, role-based access control (SUPER_ADMIN /
  FINANCE_ADMIN / OPERATIONS_ADMIN / USER)

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set DATABASE_URL, SECRET_KEY, ADMIN_SESSION_SECRET,
# ADMIN_EMAIL / ADMIN_PASSWORD (this becomes your super-admin login)

# create the database first, e.g.:
#   createdb fpl_platform

python init_db.py        # creates tables + seeds the super admin
uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Admin panel: http://localhost:8000/admin  (log in with ADMIN_EMAIL / ADMIN_PASSWORD)

## Running the full app

Three processes:

```bash
# 1. Backend API + Admin panel + Mini App static files
uvicorn app.main:app --reload

# 2. Telegram bot (separate process)
python bot.py
```

Then, in Telegram, set your bot's Mini App URL (via @BotFather → Bot Settings
→ Menu Button, or the `/app` command the bot already sends) to
`MINI_APP_URL` from your `.env` — during local dev this can point at
`http://localhost:8000/miniapp/` (served directly by the backend), but
Telegram requires **HTTPS** for a real Mini App, so for anything beyond
local testing put it behind a tunnel (ngrok/Cloudflare Tunnel) or deploy
`miniapp/` to any static host and point `MINI_APP_URL` there.

- Admin panel (CRUD): http://localhost:8000/admin
- Admin overview/stats dashboard: http://localhost:8000/admin/dashboard
- API docs: http://localhost:8000/docs
- Mini App (dev only): http://localhost:8000/miniapp/

### End-to-end flow
1. User opens the bot, taps **Start**, shares phone number, enters their FPL
   Manager ID, confirms the team the bot shows them.
2. Bot sends an **Open Mini App** button.
3. Mini App authenticates itself using Telegram's signed `initData`
   (`POST /auth/telegram-webapp`) — this is cryptographically verified
   server-side (`app/services/telegram_auth.py`), so a user can't spoof
   another user's `telegram_id`.
4. In the Mini App: Home shows wallet balance + current gameweek + a Join
   button; Wallet shows the Telebirr/CBE deposit instructions and a form to
   submit a transaction ID; Games shows this week's details; Board shows
   the live leaderboard; Profile shows FPL team + stats.
5. Admin verifies deposits and monitors everything from `/admin` and
   `/admin/dashboard`.

## Deploying to Render (free, for testing)

This repo includes `render.yaml`, a Render "Blueprint" — it deploys the
API + admin panel + Mini App static files + Telegram bot as **one free
web service**, plus a free Postgres database, in one step.

Why one service: Render's free tier has **no free Background Worker**, so
the bot can't run as its usual always-polling process for free. Instead,
the bot runs in **webhook mode** inside the same FastAPI app — Telegram
pushes updates to `/telegram/webhook` instead of the bot pulling them.
This also sidesteps needing ngrok/a tunnel: even Render's free web
services get a real `https://your-app.onrender.com` URL, and Telegram
requires HTTPS for both bot webhooks and Mini Apps.

**Steps:**

1. Push this project to a GitHub repo.
2. On Render: **New +** → **Blueprint** → connect the repo. Render reads
   `render.yaml` and provisions the web service + database automatically.
3. Before or right after the first deploy, set these in the Render
   dashboard (marked `sync: false` in `render.yaml` so they're not
   committed to git):
   - `ADMIN_PASSWORD` — your real admin login password
   - `BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
4. Deploy once. Render gives you a URL like `https://fpl-platform-xyz.onrender.com`.
5. Set two more env vars now that you know the URL, then **redeploy**:
   - `PUBLIC_BASE_URL` = `https://fpl-platform-xyz.onrender.com`
   - `MINI_APP_URL` = `https://fpl-platform-xyz.onrender.com/miniapp/`
6. In Telegram, message [@BotFather](https://t.me/BotFather) → your bot →
   **Bot Settings → Menu Button** → set the URL to your `MINI_APP_URL`.
7. Message your bot `/start` — you're testing the full flow live, for free.

**Free-tier limits to know before you rely on this for anything beyond
testing** (verify current numbers on Render's pricing page — they do
change):
- The web service **spins down after ~15 minutes idle**; the next
  request (including a Telegram webhook delivery) eats a cold-start
  delay of up to ~1 minute. Fine for solo/small-group testing, not for
  a real launch.
- The free Postgres database **expires after about 30 days**. For a
  longer test run, either recreate it before then or upgrade that one
  piece to a paid Render Postgres (cheap) while keeping the web service
  free.
- `python init_db.py` runs on every deploy/restart (see `startCommand`
  in `render.yaml`) — it's idempotent (checks before creating), so this
  is safe, just slightly slower to boot each time.

**Alternatives if Render's specifics don't fit:** Railway and Fly.io
offer similar Git-push deploys; the same webhook-mode trick (rather than
a worker) applies anywhere without a free always-on worker tier. If a
platform gives you a free always-on worker, you can skip the webhook
approach entirely and run `bot.py`'s polling script as that worker
instead — either works, webhook mode just doesn't need a second service.

## Deposit flow (Telebirr / CBE) — as requested for MVP

1. `GET /deposits/instructions` returns the two receiving accounts
   (Telebirr: configured via `TELEBIRR_RECEIVER_NUMBER`, currently
   `0961242949`; CBE: `CBE_ACCOUNT_NUMBER`, currently `1000477935471`).
2. User pays manually outside the platform, then
   `POST /deposits` with `{method, amount, transaction_id}` — e.g. a test
   value like `DIJ3USEE6B` — creating a `PENDING` deposit request.
3. A finance admin reviews it (via the admin panel or
   `POST /deposits/{id}/approve`), which credits the wallet through the
   ledger service (`credit_wallet`) — never by hand-editing a balance.
4. `POST /deposits/{id}/reject` for anything that doesn't check out.

**Duplicate transaction IDs are NOT currently rejected**
(`ENFORCE_UNIQUE_DEPOSIT_TXN_ID=false` in `.env`), exactly as requested for
test/MVP use, so you can reuse a test code like `DIJ3USEE6B` freely.

⚠️ **Before this goes live with real money**, set
`ENFORCE_UNIQUE_DEPOSIT_TXN_ID=true`. Without it, the same real payment
receipt/transaction code could be submitted by multiple users (or the same
user multiple times) and each would be manually approved and credited
separately — that's a direct way to lose money, not just a data-quality
issue.

## Security notes for this MVP

- All money movement goes through `app/services/ledger.py`
  (`credit_wallet` / `debit_wallet`) — this is the only code path allowed
  to touch a wallet balance, and every call writes an immutable
  `WalletTransaction` row plus an `AuditLog` entry.
- Passwords are bcrypt-hashed; JWTs are signed with `SECRET_KEY`. The
  admin panel's session cookie uses a **separate** secret
  (`ADMIN_SESSION_SECRET`) so a leak of one doesn't compromise the other.
- Admin roles are enforced server-side (`require_admin`,
  `require_finance_admin` in `app/deps.py`) — not just hidden in the UI.
- This build has **not been publicly released** and is not yet hardened
  for production traffic. Before any public/real-money launch:
  - Set strong, unique values for `SECRET_KEY` and `ADMIN_SESSION_SECRET`.
  - Turn on `ENFORCE_UNIQUE_DEPOSIT_TXN_ID`.
  - Put the app behind HTTPS (reverse proxy — nginx/Caddy) and restrict
    `/admin` to trusted IPs or a VPN.
  - Replace `init_db.py`'s `create_all` with Alembic migrations.
  - Add rate limiting on `/auth/login` and `/deposits` (e.g. `slowapi`) to
    slow brute-force and spam.
  - Tighten `CORSMiddleware.allow_origins` to your actual Mini App domain
    (already gated by `PLATFORM_LIVE` but double-check before launch).
  - Confirm your business license (in progress) before setting
    `PLATFORM_LIVE=true`.

## Project layout

```
app/
  main.py              FastAPI app, middleware, router + static mount
  config.py            env-based settings (pydantic-settings)
  database.py          SQLAlchemy engine/session
  models.py            ORM models — full schema from the v2 spec
  schemas.py           Pydantic request/response models
  security.py          password hashing + JWT
  deps.py              auth dependencies / RBAC guards
  admin.py             sqladmin CRUD dashboard + auth backend
  admin_dashboard.py    /admin/dashboard stats overview page
  templates/
    dashboard.html      admin stats page template
  routers/
    auth.py              admin/staff login + Mini App Telegram login
    deposits.py           Telebirr/CBE manual deposit + verification
    wallet.py              wallet balance
    fpl.py                 FPL manager lookup/confirm (rate-limited)
    users.py                profile aggregation, phone update
    gameweeks.py             current/join/leaderboard
    internal.py               bot-only endpoints (shared-secret auth)
  services/
    ledger.py               all wallet credit/debit logic (source of truth)
    fpl_client.py             official FPL API client
    fpl_sync.py                token-bucket rate limiter (50-60 req/min)
    telegram_auth.py           validates Telegram initData (HMAC)
  utils/
    audit.py                 audit log helper
miniapp/
  index.html / style.css / app.js    Telegram Mini App (vanilla JS)
bot.py                  Telegram bot process (aiogram)
init_db.py              bootstrap: create tables + seed super admin
requirements.txt
.env.example
```

## Not yet built (flagged, not silently skipped)

- Automated FPL score sync loop + prize calculation job (services stubbed;
  the state machine, `ScoreSnapshot`, `Prize`, `TieBreakRecord` models are
  in place per the v2 spec, but nothing yet writes to them automatically)
- Withdrawal payout execution (model + admin visibility exist; no payout
  provider integration yet since none was specified)
- Refund and dispute *workflow* endpoints (models exist; approve/reject
  logic like deposits has isn't written yet)
- Mini App is vanilla JS for zero build-step simplicity — fine for MVP,
  but consider React/Telegram's official UI kit if the UI grows

Tell me which of these to build next and I'll continue in the same style.
