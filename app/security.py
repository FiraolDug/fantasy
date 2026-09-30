import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

import bcrypt
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.config import settings
from app.models import utcnow

_MAX_BCRYPT_BYTES = 72
# Used to keep login timing equal whether or not the account exists.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:_MAX_BCRYPT_BYTES], bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    try:
        return bcrypt.checkpw(plain.encode()[:_MAX_BCRYPT_BYTES], (hashed or _DUMMY_HASH).encode()) and hashed is not None
    except ValueError:
        return False


def password_is_strong(pw: str) -> bool:
    return len(pw) >= 12 and any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw)


# ---- user access tokens (each bound to a revocable UserSession row) ----
def create_user_token(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    exp = utcnow() + timedelta(hours=settings.user_session_hours)
    return jwt.encode(
        {"sub": str(user_id), "sid": str(session_id), "typ": "user", "exp": exp},
        settings.secret_key, algorithm=settings.algorithm,
    )


def decode_user_token(token: str) -> dict | None:
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
    return claims if claims.get("typ") == "user" and "sub" in claims and "sid" in claims else None


# ---- admin cookie sessions ----
def new_session_secret() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---- pseudonymised IPs for user-facing events ----
def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    key = (settings.ip_hash_secret or settings.secret_key).encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()


# ---- TOTP (admin MFA) ----
def _fernet() -> Fernet:
    if not settings.field_encryption_key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is not configured")
    return Fernet(settings.field_encryption_key.encode())


def new_totp_secret() -> tuple[str, str]:
    """Returns (plain_secret, encrypted_secret_for_db)."""
    plain = pyotp.random_base32()
    return plain, _fernet().encrypt(plain.encode()).decode()


def totp_uri(plain_secret: str, email: str) -> str:
    return pyotp.TOTP(plain_secret).provisioning_uri(name=email, issuer_name="FPL Platform")


def verify_totp(encrypted_secret: str | None, code: str) -> bool:
    if not encrypted_secret or not code.isdigit() or len(code) != 6:
        return False
    try:
        plain = _fernet().decrypt(encrypted_secret.encode()).decode()
    except InvalidToken:
        return False
    return pyotp.TOTP(plain).verify(code, valid_window=1)


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
