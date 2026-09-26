"""
Validates Telegram WebApp `initData` per Telegram's official algorithm:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

This is the ONLY thing that proves a Mini App request actually came from
Telegram for the claimed telegram_id — never trust a telegram_id passed
as a plain request field.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from app.config import settings


class InvalidInitData(Exception):
    pass


def validate_init_data(init_data: str, max_age_seconds: int = 86400) -> dict:
    if not settings.bot_token:
        raise InvalidInitData("BOT_TOKEN is not configured on the server")

    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InvalidInitData("missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))

    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InvalidInitData("hash mismatch — data was not signed by this bot")

    auth_date = int(pairs.get("auth_date", 0))
    if time.time() - auth_date > max_age_seconds:
        raise InvalidInitData("initData has expired")

    user_raw = pairs.get("user")
    if not user_raw:
        raise InvalidInitData("missing user field")

    return json.loads(user_raw)
