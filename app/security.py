from datetime import datetime, timedelta

from bcrypt import checkpw, gensalt, hashpw

from jose import JWTError, jwt

from app.config import settings

# bcrypt has a hard 72-byte input limit; truncate consistently on both
# hash and verify so a long password doesn't silently behave differently
# between the two calls.
_MAX_BCRYPT_BYTES = 72


def hash_password(password: str) -> str:
    return hashpw(password.encode("utf-8")[:_MAX_BCRYPT_BYTES], gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return checkpw(
        plain_password.encode("utf-8")[:_MAX_BCRYPT_BYTES], hashed_password.encode("utf-8")
    )


def create_access_token(subject: str, role: str, expires_minutes: int | None = None) -> str:
    expire = datetime.utcnow() + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
