from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ADMIN_ROLES, get_current_user
from app.models import User
from app.schemas import Token, UserOut
from app.security import create_access_token, verify_password
from app.services.telegram_auth import InvalidInitData, validate_init_data

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramWebAppAuth(BaseModel):
    init_data: str


@router.post("/telegram-webapp", response_model=Token)
def telegram_webapp_login(payload: TelegramWebAppAuth, db: Session = Depends(get_db)):
    """
    Mini App entry point. Verifies Telegram's signed initData and issues a
    JWT as soon as the bot has verified the user's phone number.

    FPL Manager ID registration is no longer a precondition for the token —
    it's collected and validated inside the Mini App itself (via the
    existing /fpl/lookup + /fpl/confirm endpoints), so a user with a
    verified phone but no FPL team yet (a PENDING registration) is expected
    to authenticate here. The Mini App gates the rest of its UI on
    `fpl_manager_id` from /users/me/profile being null and shows its own
    registration screen until that's set.
    """
    try:
        tg_user = validate_init_data(payload.init_data)
    except InvalidInitData as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    telegram_id = str(tg_user["id"])
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Start the bot and share your phone number before opening the Mini App.",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    if not user.phone_number:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Share your phone number with the bot to complete registration.",
        )

    token = create_access_token(subject=str(user.id), role=user.role.value)
    return Token(access_token=token)


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Admin / staff login. Regular end-users authenticate via the Telegram bot
    flow (not implemented here) rather than a password.
    """
    user = db.query(User).filter(User.phone_number == form_data.username).first()
    if (
        user is None
        or user.role not in ADMIN_ROLES
        or user.password_hash is None
        or not verify_password(form_data.password, user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(subject=str(user.id), role=user.role.value)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user