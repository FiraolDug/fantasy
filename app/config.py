import os
import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Render (and Heroku-style) Postgres URLs come as postgres://...
        # SQLAlchemy's psycopg2 driver needs postgresql+psycopg2://...
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+psycopg2://", 1)
        elif v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    # Security
    secret_key: str
    admin_session_secret: str
    access_token_expire_minutes: int = 60
    algorithm: str = "HS256"

    # Bootstrap admin
    admin_email: str
    admin_password: str

    # Manual deposit destinations
    telebirr_receiver_name: str = "Platform Owner"
    telebirr_receiver_number: str
    cbe_receiver_name: str = "Platform Owner"
    cbe_account_number: str

    # MVP / test-mode flags
    enforce_unique_deposit_txn_id: bool = False
    platform_live: bool = False

    # --- Telegram ---
    bot_token: str = ""
    bot_internal_secret: str = "change_this_shared_secret"
    telegram_webhook_secret: str = ""
    # Base URL Telegram will POST updates to in webhook mode, e.g.
    # https://your-app.onrender.com — leave blank to disable webhook
    # auto-registration (e.g. when using bot.py's polling mode locally).
    public_base_url: str = ""
    # Where the Mini App is served. If not set explicitly, derived from
    # PUBLIC_BASE_URL (same host, /miniapp path) so a fresh deploy that only
    # sets PUBLIC_BASE_URL still opens the right place instead of falling
    # back to a placeholder domain.
    mini_app_url: str = ""
    # Where the bot calls the backend internally. In production this is
    # the same process/service (loopback). Leave blank to auto-derive from
    # Render's $PORT env var; only set this explicitly for non-Render setups.
    backend_internal_url: str = ""

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _validate_telegram_webhook_secret(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
            raise ValueError(
                "TELEGRAM_WEBHOOK_SECRET must contain 1-256 letters, digits, underscores, or hyphens"
            )
        return value

    @field_validator("mini_app_url")
    @classmethod
    def _default_mini_app_url(cls, v: str, info) -> str:
        if v:
            return v
        public_base_url = info.data.get("public_base_url") or ""
        if public_base_url:
            return f"{public_base_url.rstrip('/')}/miniapp"
        # No explicit value and nothing to derive from. This used to fall
        # back to a placeholder ("example.com"), which silently sent real
        # users to a domain that isn't yours — the bug this replaces. Fail
        # loudly at startup instead, but only if the bot is actually
        # enabled; a bare API deployment with no BOT_TOKEN has no use for
        # this setting at all.
        if info.data.get("bot_token"):
            raise ValueError(
                "MINI_APP_URL is not set and PUBLIC_BASE_URL is empty, so there's "
                "nowhere to point the bot's Mini App button. Set MINI_APP_URL "
                "(or PUBLIC_BASE_URL, which MINI_APP_URL is derived from) and redeploy."
            )
        return v

    @field_validator("backend_internal_url")
    @classmethod
    def _default_backend_internal_url(cls, v: str) -> str:
        if v:
            return v
        port = os.environ.get("PORT", "8000")
        return f"http://localhost:{port}"


settings = Settings()
