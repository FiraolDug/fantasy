"""
Telegram bot logic (aiogram). Imported by app/main.py and run in
**webhook mode** in production (single free Render web service, no
separate Background Worker needed — see bot.py for local polling dev use).

Registration flow:
    /start -> Telegram native "Share my phone number" -> bot verifies and
    stores the contact (PENDING registration) -> bot hands off to the Mini
    App, which authenticates the user and collects/validates the FPL
    Manager ID itself (see miniapp/index.html + app/routers/auth.py +
    the existing /fpl/lookup + /fpl/confirm endpoints). The bot never asks
    for the Manager ID in chat.
"""
import logging

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.config import settings

logger = logging.getLogger(__name__)

# Internal calls to our own backend. In production this is the SAME Render
# service (loop back over localhost), since the API and the bot webhook
# both run inside one FastAPI process — no second service needed for free
# hosting. Overridable via BACKEND_INTERNAL_URL for other setups.
BACKEND_URL = settings.backend_internal_url

bot = Bot(token=settings.bot_token) if settings.bot_token else None
dp = Dispatcher(storage=MemoryStorage())


def _headers():
    return {"X-Bot-Secret": settings.bot_internal_secret}


async def _registration_status(telegram_id: int):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BACKEND_URL}/internal/registration/{telegram_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


def _open_app_keyboard(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, web_app=WebAppInfo(url=settings.mini_app_url))]
        ]
    )


def _contact_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Share my phone number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Welcome to the Weekly FPL Competition!\n\n"
        "To continue, please share your phone number.",
        reply_markup=_contact_request_keyboard(),
    )


@dp.message(F.contact)
async def got_contact(message: Message):
    # Telegram lets a user forward someone else's contact card; only accept
    # a contact that matches the sender, so we never store a phone number
    # for anyone other than the person who tapped the share button.
    if message.contact.user_id != message.from_user.id:
        await message.answer(
            "Please use the Share my phone number button to share your own number.",
        )
        return

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BACKEND_URL}/internal/register",
            headers=_headers(),
            json={
                "telegram_id": str(message.from_user.id),
                "phone_number": message.contact.phone_number,
                "full_name": message.from_user.full_name,
            },
        )
        resp.raise_for_status()

    await message.answer(
        "Thanks! Your phone number has been verified.",
        reply_markup=ReplyKeyboardRemove(),
    )

    try:
        registration = await _registration_status(message.from_user.id)
    except httpx.HTTPError:
        logger.exception("Could not check Telegram registration after contact sharing")
        await message.answer(
            "Your phone number was saved, but registration status could not be "
            "checked. Please send /start to continue."
        )
        return

    # Whatever comes next (Manager ID entry, or just opening the app if
    # that's already done) is asked for inside the Mini App itself, not here
    # in chat.
    label = "Open Mini App" if registration["team_registered"] else "Continue"
    await message.answer("Tap below to continue.", reply_markup=_open_app_keyboard(label))


@dp.message(F.text == "/app")
async def open_app(message: Message):
    try:
        registration = await _registration_status(message.from_user.id)
    except httpx.HTTPError:
        registration = None

    if registration and registration["team_registered"]:
        await message.answer(
            "Open the Mini App:",
            reply_markup=_open_app_keyboard("Open Mini App"),
        )
        return

    # No verified phone/team on record yet (or the status check failed) —
    # run the normal /start flow instead of assuming they're registered.
    await start(message)