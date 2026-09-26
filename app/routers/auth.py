from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ADMIN_ROLES, get_current_user
from app.models import User, UserRole
from app.schemas import Token, UserOut
from app.security import create_access_token, verify_password
from app.services.telegram_auth import InvalidInitData, validate_init_data

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramWebAppAuth(BaseModel):
    init_data: str


@router.post("/telegram-webapp", response_model=Token)
def telegram_webapp_login(payload: TelegramWebAppAuth, db: Session = Depends(get_db)):
    """
    Mini App entry point. Verifies Telegram's signed initData, then finds or
    creates the corresponding User and issues a normal (role=USER) JWT for
    all subsequent API calls from the Mini App.
    """
    try:
        tg_user = validate_init_data(payload.init_data)
    except InvalidInitData as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    telegram_id = str(tg_user["id"])
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None:
        full_name = " ".join(
            filter(None, [tg_user.get("first_name"), tg_user.get("last_name")])
        ) or None
        user = User(telegram_id=telegram_id, full_name=full_name, role=UserRole.USER)
        db.add(user)
        db.commit()
        db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

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
