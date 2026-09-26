from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.admin import register_admin
from app.admin_dashboard import router as admin_dashboard_router
from app.config import settings
from app.routers import auth, deposits, fpl, gameweeks, internal, users, wallet

app = FastAPI(title="FPL Telegram Competition Platform", version="0.1.0")

# Session middleware backs the admin panel's login session (separate secret
# from the API's JWT signing key, so compromising one doesn't compromise both).
app.add_middleware(SessionMiddleware, secret_key=settings.admin_session_secret)

# CORS: tighten allow_origins to your actual Mini App domain before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.platform_live else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(deposits.router)
app.include_router(wallet.router)
app.include_router(fpl.router)
app.include_router(users.router)
app.include_router(gameweeks.router)
app.include_router(internal.router)
app.include_router(admin_dashboard_router)

register_admin(app, secret_key=settings.admin_session_secret)

# Serves the Mini App's static files at /miniapp — convenient for local
# dev and small deployments. For real traffic, serve miniapp/ from a CDN
# or static host instead and just point MINI_APP_URL at that instead.
app.mount("/miniapp", StaticFiles(directory="miniapp", html=True), name="miniapp")


# --- Telegram bot: webhook mode ---
# Runs inside this same process/service so a free host (no Background
# Worker tier) only needs ONE deployable service for API + admin +
# Mini App + bot. Local dev can use bot.py's polling mode instead.
if settings.bot_token:
    from app.telegram_bot import bot as tg_bot
    from app.telegram_bot import dp as tg_dp
    from aiogram.types import Update

    @app.on_event("startup")
    async def _set_telegram_webhook():
        if settings.public_base_url:
            webhook_url = f"{settings.public_base_url.rstrip('/')}/telegram/webhook"
            await tg_bot.set_webhook(webhook_url, drop_pending_updates=False)

    @app.post("/telegram/webhook")
    async def telegram_webhook(request: Request):
        data = await request.json()
        update = Update.model_validate(data, context={"bot": tg_bot})
        await tg_dp.feed_update(tg_bot, update)
        return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok", "platform_live": settings.platform_live}
