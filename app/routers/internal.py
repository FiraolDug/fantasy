"""
Called only by the Telegram bot process, authenticated with a shared secret.
Never expose these routes through a public proxy rule if you can avoid it.
"""
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import FraudAlert, Severity, User, utcnow
from app.security import safe_equal
from app.services.audit import security_event
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/internal", tags=["internal-bot"], dependencies=[Depends(rate_limit("internal", 120, 60))])
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def require_bot_secret(x_bot_secret: str = Header(default="")):
    if not x_bot_secret or not safe_equal(x_bot_secret, settings.bot_internal_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bot secret")


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    telegram_id: str = Field(pattern=r"^[0-9]{1,20}$")
    contact_user_id: str = Field(pattern=r"^[0-9]{1,20}$")   # Telegram's contact.user_id
    phone_number: str = Field(min_length=8, max_length=20)
    full_name: str | None = Field(default=None, max_length=255)


def _e164(raw: str) -> str | None:
    digits = re.sub(r"[^0-9]", "", raw)
    phone = "+" + digits
    return phone if _E164.fullmatch(phone) else None


@router.get("/registration/{telegram_id}", dependencies=[Depends(require_bot_secret)])
def registration_status(telegram_id: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.telegram_id == telegram_id[:20]).first()
    return {"phone_registered": bool(user and user.is_phone_verified),
            "team_registered": bool(user and user.fpl_team is not None)}


@router.post("/register", dependencies=[Depends(require_bot_secret)])
def internal_register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Stores a phone number Telegram itself attested belongs to this Telegram account."""
    if payload.contact_user_id != payload.telegram_id:
        raise HTTPException(400, "The shared contact does not belong to this Telegram account.")
    phone = _e164(payload.phone_number)
    if phone is None:
        raise HTTPException(422, "Invalid phone number")

    user = db.query(User).filter(User.telegram_id == payload.telegram_id).first()
    if user is None:
        user = User(telegram_id=payload.telegram_id, full_name=payload.full_name, telegram_verified_at=utcnow())
        db.add(user)
        db.flush()

    if user.phone_number and user.phone_number != phone:
        # A verified number can't be swapped by chat alone — that's an account-takeover path.
        security_event(db, request, "phone_change_blocked", severity=Severity.HIGH, user_id=user.id)
        db.commit()
        raise HTTPException(409, "This account already has a different verified phone number. Contact support.")

    other = db.query(User).filter(User.phone_number == phone, User.id != user.id).first()
    if other is not None:
        db.add(FraudAlert(category="duplicate_phone", severity=Severity.HIGH, related_user_id=user.id,
                          description="Telegram account tried to verify a phone number already used by another account.",
                          details={"other_user": str(other.id)}))
        security_event(db, request, "duplicate_phone", severity=Severity.HIGH, user_id=user.id)
        db.commit()
        raise HTTPException(409, "This phone number is already linked to another account.")

    user.phone_number, user.phone_verified_at = phone, user.phone_verified_at or utcnow()
    if payload.full_name and not user.full_name:
        user.full_name = payload.full_name
    security_event(db, request, "phone_verified", user_id=user.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This phone number is already linked to another account.")
    return {"user_id": str(user.id)}
