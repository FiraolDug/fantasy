"""
LOCAL DEV ONLY: runs the bot in polling mode against a backend you're
running separately (e.g. `uvicorn app.main:app --reload` on localhost).

    python bot.py

In production (Render or any real deploy) the bot runs in WEBHOOK mode
INSIDE the FastAPI app instead — see app/main.py's /telegram/webhook
route and PUBLIC_BASE_URL in .env. Do not run this script and the
webhook at the same time; Telegram only allows one active update source
per bot token.
"""
import asyncio

from app.telegram_bot import bot, dp


async def main():
    if bot is None:
        raise RuntimeError("BOT_TOKEN is not set in .env")
    await bot.delete_webhook(drop_pending_updates=True)  # polling and webhooks can't run together
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
