import hmac

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import (
    admin_api, admin_auth, auth, deposits, gameweeks, internal, posts, referrals, users, verification, wallet, withdrawals,
)

app = FastAPI(
    title="FPL Platform", version="1.0.0",
    docs_url=None if settings.platform_live else "/docs",
    redoc_url=None, openapi_url=None if settings.platform_live else "/openapi.json",
)

MAX_BODY_BYTES = 64 * 1024

_CSP_ADMIN = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_CSP_MINIAPP = ("default-src 'none'; script-src 'self' 'unsafe-inline' https://telegram.org; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors https://web.telegram.org https://*.telegram.org")
_CSP_API = "default-src 'none'; frame-ancestors 'none'"


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    path = request.url.path
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path.startswith("/admin-ui"):
        h["Content-Security-Policy"], h["X-Frame-Options"] = _CSP_ADMIN, "DENY"
    elif path.startswith("/miniapp"):
        h["Content-Security-Policy"] = _CSP_MINIAPP
    elif not path.startswith(("/docs", "/openapi")):
        h["Content-Security-Policy"] = _CSP_API
        h["Cache-Control"] = "no-store"      # API responses hold personal data: never cache them
    if settings.platform_live:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,          # exact origins only; empty = same-origin only
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-CSRF-Token"],
)

for r in (auth, referrals, verification, users, wallet, deposits, withdrawals, gameweeks, posts, internal, admin_auth, admin_api):
    app.include_router(r.router)

app.mount("/miniapp", StaticFiles(directory="miniapp", html=True), name="miniapp")
app.mount("/admin-ui", StaticFiles(directory="admin-ui", html=True), name="admin-ui")

if settings.bot_token:
    from aiogram.types import Update

    from app.telegram_bot import bot as tg_bot
    from app.telegram_bot import dp as tg_dp

    @app.on_event("startup")
    async def _set_telegram_webhook():
        if settings.public_base_url:
            if not settings.telegram_webhook_secret:
                raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is required when PUBLIC_BASE_URL is set")
            await tg_bot.set_webhook(f"{settings.public_base_url.rstrip('/')}/telegram/webhook",
                                     secret_token=settings.telegram_webhook_secret, drop_pending_updates=False)

    @app.post("/telegram/webhook", include_in_schema=False)
    async def telegram_webhook(request: Request, secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token")):
        if not settings.telegram_webhook_secret or secret_token is None or not hmac.compare_digest(
                secret_token.encode(), settings.telegram_webhook_secret.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Telegram webhook secret")
        update = Update.model_validate(await request.json(), context={"bot": tg_bot})
        await tg_dp.feed_update(tg_bot, update)
        return {"ok": True}


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
