# FPL Platform

FastAPI + SQLAlchemy backend, Telegram bot, Mini App (`miniapp/`), admin panel (`admin-ui/`).

## Run

```
cp .env.example .env   # fill in secrets
pip install -r requirements.txt
python init_db.py      # creates tables, roles, first super admin (prints TOTP link once)
uvicorn app.main:app
pytest                 # 24 tests: verification, isolation, admin, money
```

See `SECURITY_REVIEW.md` before release. Admin panel: `/admin-ui/`. Verification screen: `/miniapp/onboarding.html`.
