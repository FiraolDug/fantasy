"""
Endpoints called ONLY by the Telegram bot process (a trusted backend
service, not an end-user client), authenticated with a shared secret
header rather than a user JWT. Never expose these to the Mini App or
any public client.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import FPLTeam, User, UserRole
from app.schemas import FPLTeamOut
from app.services.fpl_client import FPLManagerNotFound, fetch_manager_entry
from app.services.fpl_sync import fpl_rate_limiter

router = APIRouter(prefix="/internal", tags=["internal-bot"])


def require_bot_secret(x_bot_secret: str = Header(...)):
    if x_bot_secret != settings.bot_internal_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bot secret")


from pydantic import BaseModel  # noqa: E402


class RegisterRequest(BaseModel):
    telegram_id: str
    phone_number: str | None = None
    full_name: str | None = None


class FPLConfirmRequest(BaseModel):
    telegram_id: str
    manager_id: str


@router.post("/register", dependencies=[Depends(require_bot_secret)])
def internal_register(payload: RegisterRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.telegram_id == payload.telegram_id).first()
    if user is None:
        user = User(
            telegram_id=payload.telegram_id,
            phone_number=payload.phone_number,
            full_name=payload.full_name,
            role=UserRole.USER,
        )
        db.add(user)
    else:
        if payload.phone_number:
            user.phone_number = payload.phone_number
        if payload.full_name:
            user.full_name = payload.full_name
    db.commit()
    db.refresh(user)
    return {"user_id": str(user.id)}


@router.post("/fpl-confirm", response_model=FPLTeamOut, dependencies=[Depends(require_bot_secret)])
def internal_fpl_confirm(payload: FPLConfirmRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.telegram_id == payload.telegram_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not registered yet")

    existing_owner = db.query(FPLTeam).filter(FPLTeam.manager_id == payload.manager_id).first()
    if existing_owner is not None and existing_owner.user_id != user.id:
        raise HTTPException(
            status_code=409, detail="This FPL Manager ID is already registered to another account."
        )

    fpl_rate_limiter.acquire()
    try:
        info = fetch_manager_entry(payload.manager_id)
    except FPLManagerNotFound:
        raise HTTPException(status_code=404, detail="No FPL manager found with that ID")

    team = db.query(FPLTeam).filter(FPLTeam.user_id == user.id).first()
    if team is None:
        team = FPLTeam(user_id=user.id, manager_id=info["manager_id"])
        db.add(team)

    team.manager_id = info["manager_id"]
    team.team_name = info["team_name"]
    team.manager_name = info["manager_name"]
    team.verified = True

    db.commit()
    db.refresh(team)
    return team
