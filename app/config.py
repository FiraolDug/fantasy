import os
import re

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK = {"", "change_this_shared_secret", "change_me", "secret"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database (PostgreSQL or MySQL/MariaDB) ---
    database_url: str

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        for prefix, target in (
            ("postgres://", "postgresql+psycopg2://"),
            ("postgresql://", "postgresql+psycopg2://"),
            ("mysql://", "mysql+pymysql://"),
            ("mariadb://", "mysql+pymysql://"),
        ):
            if v.startswith(prefix):
                return v.replace(prefix, target, 1)
        return v

    # --- Secrets (each one must be different and >= 32 chars in live mode) ---
    secret_key: str                       # signs user access tokens
    ip_hash_secret: str = ""              # HMAC key for IP pseudonymisation
    field_encryption_key: str = ""        # Fernet key for admin TOTP secrets
    bot_internal_secret: str = "change_this_shared_secret"

    # --- Sessions ---
    user_session_hours: int = 12
    telegram_init_data_max_age_seconds: int = 3600
    admin_session_idle_minutes: int = 30
    admin_session_absolute_hours: int = 8
    admin_mfa_required: bool = True
    cookie_secure: bool = True            # set false only for plain-http local dev
    algorithm: str = "HS256"

    # --- Network ---
    cors_allowed_origins: str = ""        # comma separated, exact origins only
    trusted_proxy_count: int = 1          # hops of X-Forwarded-For you trust (0 = none)

    # --- Verification policy ---
    fpl_required_country: str = "ET"
    require_ownership_challenge: bool = True
    team_name_case_sensitive: bool = True
    verification_max_failed_per_day: int = 5
    verification_max_manager_ids_per_day: int = 3
    verification_max_attempts_per_ip_hour: int = 30
    challenge_ttl_minutes: int = 30
    require_ethiopian_phone: bool = False  # false = only flag non-+251 numbers for review

    # --- Bootstrap admin ---
    admin_email: str
    admin_password: str

    # --- Manual deposit destinations ---
    telebirr_receiver_name: str = "Platform Owner"
    telebirr_receiver_number: str
    cbe_receiver_name: str = "Platform Owner"
    cbe_account_number: str
    min_deposit: int = 50
    max_deposit: int = 50000
    min_withdrawal: int = 100
    max_withdrawal_per_day: int = 20000

    # --- Referrals ---
    referral_reward: int = 10                    # ETB per referred person
    referral_trigger: str = "first_entry"        # "first_entry" (referee pays a gameweek entry) or "verified"
    referral_max_rewards_per_user: int = 50
    referral_max_account_age_days: int = 7       # a code can only be applied by a new account
    referral_link_base: str = ""                 # e.g. https://t.me/YourBot/app  (code is appended as ?startapp=CODE)

    platform_live: bool = False

    # --- Telegram ---
    bot_token: str = ""
    telegram_webhook_secret: str = ""
    public_base_url: str = ""
    mini_app_url: str = ""
    backend_internal_url: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @field_validator("telegram_webhook_secret")
    @classmethod
    def _validate_telegram_webhook_secret(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
            raise ValueError("TELEGRAM_WEBHOOK_SECRET must contain 1-256 letters, digits, _ or -")
        return value

    @field_validator("mini_app_url")
    @classmethod
    def _default_mini_app_url(cls, v: str, info) -> str:
        if v:
            return v
        base = info.data.get("public_base_url") or ""
        if base:
            return f"{base.rstrip('/')}/miniapp"
        if info.data.get("bot_token"):
            raise ValueError("MINI_APP_URL or PUBLIC_BASE_URL must be set when BOT_TOKEN is set")
        return v

    @field_validator("backend_internal_url")
    @classmethod
    def _default_backend_internal_url(cls, v: str) -> str:
        return v or f"http://localhost:{os.environ.get('PORT', '8000')}"

    @field_validator("referral_trigger")
    @classmethod
    def _trigger(cls, v: str) -> str:
        if v not in ("first_entry", "verified"):
            raise ValueError("REFERRAL_TRIGGER must be first_entry or verified")
        return v

    @model_validator(mode="after")
    def _live_mode_guards(self):
        if not self.platform_live:
            return self
        problems = []
        secrets = {
            "SECRET_KEY": self.secret_key,
            "IP_HASH_SECRET": self.ip_hash_secret,
            "BOT_INTERNAL_SECRET": self.bot_internal_secret,
        }
        for name, val in secrets.items():
            if val in _WEAK or len(val) < 32:
                problems.append(f"{name} must be a random string of at least 32 characters")
        if len(set(secrets.values())) != len(secrets):
            problems.append("SECRET_KEY, IP_HASH_SECRET and BOT_INTERNAL_SECRET must all differ")
        if not self.field_encryption_key:
            problems.append("FIELD_ENCRYPTION_KEY is required (Fernet key)")
        if not self.cors_origins or "*" in self.cors_origins:
            problems.append("CORS_ALLOWED_ORIGINS must list exact origins (no wildcard)")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true")
        if not self.public_base_url.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must be an https URL")
        if not self.admin_mfa_required:
            problems.append("ADMIN_MFA_REQUIRED must be true")
        if not self.require_ownership_challenge:
            problems.append("REQUIRE_OWNERSHIP_CHALLENGE must be true (ID + team name are public data)")
        if problems:
            raise ValueError("Unsafe live configuration: " + "; ".join(problems))
        return self


settings = Settings()
