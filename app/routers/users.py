from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    CompetitionEntry, EntryStatus, Prize, ScoreSnapshot, User, Wallet, WalletTransaction, WalletTxnType,
)
from app.services.settings import get_platform_settings

router = APIRouter(prefix="/users", tags=["users"])


class ProfileOut(BaseModel):
    full_name: str | None
    phone_verified: bool
    verified: bool
    fpl_team_name: str | None
    fpl_manager_id: str | None
    total_gameweeks: int
    total_points: int
    best_rank: int | None
    total_winnings: Decimal
    total_withdrawals: Decimal
    available_balance: Decimal
    ads_enabled: bool


@router.get("/me/profile", response_model=ProfileOut)
def my_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything here is scoped to the signed-in user by user.id from their session."""
    team, wallet = user.fpl_team, user.wallet
    entries = db.query(CompetitionEntry.id).filter(
        CompetitionEntry.user_id == user.id, CompetitionEntry.status == EntryStatus.CONFIRMED).all()
    entry_ids = [e[0] for e in entries]

    total_points, best_rank = 0, None
    for eid in entry_ids:
        latest = db.query(ScoreSnapshot).filter(ScoreSnapshot.competition_entry_id == eid) \
            .order_by(ScoreSnapshot.captured_at.desc()).first()
        if latest:
            total_points += latest.points
            if latest.rank is not None:
                best_rank = latest.rank if best_rank is None else min(best_rank, latest.rank)

    winnings = db.query(func.coalesce(func.sum(Prize.amount), 0)).filter(Prize.user_id == user.id).scalar()
    withdrawn = Decimal("0")
    if wallet is not None:
        withdrawn = db.query(func.coalesce(func.sum(-WalletTransaction.amount), 0)).filter(
            WalletTransaction.wallet_id == wallet.id, WalletTransaction.type == WalletTxnType.WITHDRAWAL).scalar()

    return ProfileOut(
        full_name=user.full_name, phone_verified=user.is_phone_verified, verified=team is not None,
        fpl_team_name=team.team_name if team else None, fpl_manager_id=team.manager_id if team else None,
        total_gameweeks=len(entry_ids), total_points=total_points, best_rank=best_rank,
        total_winnings=winnings or Decimal("0"), total_withdrawals=withdrawn or Decimal("0"),
        available_balance=wallet.available_balance if wallet else Decimal("0"),
        ads_enabled=get_platform_settings(db).ads_enabled,
    )
