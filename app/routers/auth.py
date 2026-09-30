import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Severity, User, UserSession, utcnow
from app.schemas import TelegramWebAppAuth, Token
from app.security import create_user_token, decode_user_token, ip_hash
from app.services.audit import security_event
from app.services.rate_limit import client_ip, rate_limit
from app.services.telegram_auth import InvalidInitData, validate_init_data

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


@router.post("/telegram-webapp", response_model=Token, dependencies=[Depends(rate_limit("tg-login", 20, 60))])
def telegram_webapp_login(payload: TelegramWebAppAuth, request: Request, db: Session = Depends(get_db)):
    """
    Signs the user in from Telegram's signed initData (HMAC checked with the bot token).
    A phone number is NOT required to sign in: the Mini App collects it next, through
    Telegram's contact-sharing prompt, and the bot marks it verified.
    """
    try:
        tg_user = validate_init_data(payload.init_data)
    except InvalidInitData:
        security_event(db, request, "telegram_login_rejected", severity=Severity.MEDIUM)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Open the app from Telegram to sign in.")

    telegram_id = str(int(tg_user["id"]))
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None:
        name = " ".join(filter(None, [str(tg_user.get("first_name", ""))[:100], str(tg_user.get("last_name", ""))[:100]]))
        user = User(telegram_id=telegram_id, full_name=name or None, telegram_verified_at=utcnow(),
                    preferred_language=str(tg_user.get("language_code", ""))[:8] or None)
        db.add(user)
        try:
            db.flush()
        except IntegrityError:            # two first-launch requests raced
            db.rollback()
            user = db.query(User).filter(User.telegram_id == telegram_id).one()
    if not user.is_active:
        security_event(db, request, "login_blocked_inactive", user_id=user.id)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not active.")
    if user.telegram_verified_at is None:
        user.telegram_verified_at = utcnow()

    sess = UserSession(user_id=user.id, ip_hash=ip_hash(client_ip(request)),
                       user_agent=request.headers.get("user-agent", "")[:255],
                       expires_at=utcnow() + timedelta(hours=settings.user_session_hours))
    db.add(sess)
    user.last_login_at = utcnow()
    db.flush()
    token = create_user_token(user.id, sess.id)
    db.commit()
    return Token(access_token=token, expires_in=settings.user_session_hours * 3600)


@router.post("/logout", status_code=204)
def logout(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)):
    claims = decode_user_token(creds.credentials) if creds else None
    if claims:
        sess = db.get(UserSession, uuid.UUID(claims["sid"]))
        if sess and sess.revoked_at is None:
            sess.revoked_at = utcnow()
            db.commit()
