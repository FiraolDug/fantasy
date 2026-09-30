# Pre-release security review

Scope: sign-in, phone + Telegram verification, FPL team verification, wallet, admin panel.
Status of each item is what the code does now. Items under "Before you go live" are yours to do.

## The one thing to understand first

An FPL Manager ID and team name are **public**. Both appear in every league table, so "ID + exact name match"
proves someone knows public facts, not that they own the account. Anyone could type a top manager's ID and name.

So the flow has a third, server-checked step (`REQUIRE_OWNERSHIP_CHALLENGE=true`, forced on when `PLATFORM_LIVE=true`):
the user renames their FPL team to a one-time code (`FPL-XXXXXX`, 30 min) and the server sees that code through FPL's API.
Only the real account holder can do that. Without this step, your requirements 2 and 3 can be bypassed by any user with a browser.
Set it to `false` only if you accept that risk (the app refuses to start live with it off).

## Flow, as enforced by the server

1. Telegram sign-in: `initData` HMAC verified with the bot token, max age 1 h, size-capped. Session row created (revocable).
2. Phone: only the bot can set it (shared secret), only when Telegram says the contact's `user_id` equals the sender. A number can't be reused by another account or swapped once set.
3. Manager ID: format checked, FPL fetched, must report country `ET`. Not found and not-Ethiopia give the same answer.
4. Team name: FPL fetched again for the ID **stored on the server** (the client can't switch IDs mid-flow). Exact match after Unicode/whitespace normalisation; case-sensitive by default. Mismatch = clear warning, nothing linked.
5. Ownership code check, then the `fpl_teams` row is created. No FPL data is stored before this point.
6. `UNIQUE(user_id)` and `UNIQUE(manager_id)` on `fpl_teams` make double-claims impossible even under race conditions.

## Findings fixed in the code you sent

| # | Risk | Fix |
|---|------|-----|
| 1 | `/fpl/lookup` returned the team name and manager name for any ID: anyone could scrape other people's data | Endpoint removed. Steps never echo FPL data; only the caller's own linked team is ever returned |
| 2 | Leaderboard returned other users' team names and scores | Returns the caller's own standing only. History endpoint returns aggregates only |
| 3 | Team name was compared on the client / trusted from the request | All comparison is server-side against a fresh FPL fetch |
| 4 | Phone number came from the bot without proof it belonged to that Telegram user | `contact_user_id == telegram_id` enforced; duplicates raise a fraud alert |
| 5 | Stateless JWTs: no logout or revocation | Token carries a session id checked in the DB; suspending a user revokes sessions |
| 6 | Admin used a token/cookie without CSRF, no MFA, no roles | HttpOnly + SameSite=Strict cookie, per-session CSRF header, TOTP required, DB-backed roles/permissions, lockout after 5 failures, idle + absolute expiry |
| 7 | No audit trail | `audit_logs` written in the same transaction as each change; sensitive reads (full phone) are audited too |
| 8 | Old admin dashboards with inline scripts | Replaced. Admin UI has a strict CSP (`script-src 'self'`, no inline) and renders text only |
| 9 | Ledger rolled back the whole transaction on a duplicate key | Uses a savepoint; wallet `CHECK >= 0`, unique idempotency keys |
| 10 | Withdrawals could be double-submitted | Mandatory `Idempotency-Key`, per-day limit, funds moved to `pending` atomically, reject refunds through the ledger |
| 11 | Deposit receipts could be claimed twice | `UNIQUE(method, transaction_id)` |
| 12 | Secrets defaulted to weak values | In live mode the app refuses to start unless secrets are 32+ chars and different, CORS is explicit, cookies are secure, MFA and the ownership check are on |
| 13 | `innerHTML` with team names in the Mini App | Escaped; onboarding and admin build DOM with `textContent` |
| 14 | API docs exposed in production | Disabled when `PLATFORM_LIVE=true`; `Cache-Control: no-store` on API responses |

## Fraud and abuse controls

- Failed verifications: 5 per user per 24 h, then locked. Max 3 different Manager IDs per user per 24 h (enumeration guard, raises a HIGH alert).
- Per-network limit on attempts (IPs are stored only as HMAC hashes).
- ID+name passed for an already-linked manager raises a HIGH alert for admins.
- Alerts for: duplicate phone, 3+ accounts verified from one network in a day, non-+251 phone with an Ethiopian team.
- Every attempt is stored (`fpl_verification_attempts`) and every security-relevant event (`security_events`).
- Ethiopia is enforced from FPL's own registered country, not from anything the user types.

## Referrals (10 ETB per person)

Cash rewards attract fake-account farming, so the reward is not paid at sign-up.
- Paid when the referred friend **joins their first paid gameweek** (`REFERRAL_TRIGGER=first_entry`). Set `verified` to pay when they link their FPL team instead: simpler, but easier to farm.
- A code can be used once, by a new account (under 7 days old) that has no FPL team and no entries; never your own code.
- Rewards are capped per referrer (50 by default), paid through the ledger with an idempotency key (never twice).
- If referrer and friend ever signed in from the same network, the reward is **held** and shows up in Admin > Referrals for approve/void (both audited). Friends on the same Wi-Fi or mobile carrier can trigger this, so expect some legitimate holds.
- Referral money is a normal wallet credit. If you don't want it withdrawable straight away, add a rule before enabling withdrawals.

## Before you go live (needs you)

- **Rate limiter is in-memory.** Fine for one instance. With several instances, move it to Redis.
- **Database roles:** the app user should get INSERT/SELECT only on `audit_logs`, `security_events`, `wallet_transactions`. Enable TLS to the database and backups.
- **Reverse proxy:** set `TRUSTED_PROXY_COUNT` to the real number of proxies, and block `/internal/*` at the edge if the bot runs on the same host.
- **Secrets:** rotate the ones in your repo history (the zip included a `.git` folder). Put real Telebirr/CBE values only in the host's secret store.
- **FPL API:** it is unofficial and can change or rate-limit you. The code fails closed (users see "FPL isn't responding") but you should monitor it.
- **Migrations:** use Alembic (`alembic revision --autogenerate`) after the first release instead of `create_all`.
- **Legal:** paid entry with cash prizes is regulated in many places. Check Ethiopian requirements for fantasy contests / prize competitions and for holding user funds before launching. I can't advise on that.
- **Pen-test the deployed system** once: sessions, the bot webhook, and the admin login are the highest-value targets.

## Moving to MySQL later

Change `DATABASE_URL` to `mysql+pymysql://user:pass@host/db?charset=utf8mb4`, add `PyMySQL`, run `init_db.py`.
Needs MySQL 8.0.16+ (CHECK constraints are enforced) or MariaDB 10.5+. The models use only portable types (UUID, JSON, Numeric, VARCHAR enums);
the DDL was compiled against both dialects. `schema.prisma` is the same design: change `provider` to `"mysql"`.
