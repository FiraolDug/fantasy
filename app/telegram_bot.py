"""
Telegram bot logic (aiogram). Imported by app/main.py and run in
**webhook mode** in production (single free Render web service, no
separate Background Worker needed — see bot.py for local polling dev use).
"""
import logging

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
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


class Registration(StatesGroup):
    waiting_for_contact = State()
    waiting_for_manager_id = State()
    waiting_for_confirmation = State()


def _headers():
    return {"X-Bot-Secret": settings.bot_internal_secret}


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.set_state(Registration.waiting_for_contact)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Share my phone number", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Welcome to the Weekly FPL Competition!\n\n"
        "To register, please share your phone number.",
        reply_markup=kb,
    )


@dp.message(Registration.waiting_for_contact, F.contact)
async def got_contact(message: Message, state: FSMContext):
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

    await state.set_state(Registration.waiting_for_manager_id)
    await message.answer(
        "Thanks! Now enter your FPL Manager ID.\n"
        "You can find this in the URL of your FPL points page, e.g. "
        "fantasy.premierleague.com/entry/<b>7410729</b>/event/6",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )


@dp.message(Registration.waiting_for_manager_id, F.text)
async def got_manager_id(message: Message, state: FSMContext):
    manager_id = message.text.strip()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{BACKEND_URL}/fpl/lookup", json={"manager_id": manager_id})

    if resp.status_code == 404:
        await message.answer("No FPL manager found with that ID. Please try again.")
        return
    resp.raise_for_status()
    info = resp.json()

    await state.update_data(manager_id=manager_id)
    await state.set_state(Registration.waiting_for_confirmation)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yes, that's my team", callback_data="confirm_team"),
                InlineKeyboardButton(text="❌ No, try again", callback_data="retry_team"),
            ]
        ]
    )
    await message.answer(
        f"<b>FPL Team:</b> {info['team_name']}\n"
        f"<b>Manager:</b> {info['manager_name']}\n"
        f"<b>Manager ID:</b> {info['manager_id']}\n\n"
        "Is this your team?",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "retry_team")
async def retry_team(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Registration.waiting_for_manager_id)
    await callback.message.answer("No problem — enter your FPL Manager ID again.")
    await callback.answer()


@dp.callback_query(F.data == "confirm_team")
async def confirm_team(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    manager_id = data.get("manager_id")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BACKEND_URL}/internal/fpl-confirm",
            headers=_headers(),
            json={"telegram_id": str(callback.from_user.id), "manager_id": manager_id},
        )

    if resp.status_code == 409:
        await callback.message.answer(
            "That FPL Manager ID is already registered to another account. "
            "Contact support if you believe this is a mistake."
        )
        await callback.answer()
        return
    resp.raise_for_status()

    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Open Mini App", web_app=WebAppInfo(url=settings.mini_app_url))]
        ]
    )
    await callback.message.answer(
        "You're all set! Open the Mini App to fund your wallet and join this "
        "week's competition.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.message(F.text == "/app")
async def open_app(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Open Mini App", web_app=WebAppInfo(url=settings.mini_app_url))]
        ]
    )
    await message.answer("Tap below to open the app:", reply_markup=kb)
