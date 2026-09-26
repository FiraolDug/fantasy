from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    CompetitionEntry,
    EntryStatus,
    FPLTeam,
    Prize,
    ScoreSnapshot,
    User,
    Wallet,
    WalletTransaction,
    WalletTxnType,
)

router = APIRouter(prefix="/users", tags=["users"])


class PhoneUpdate(BaseModel):
    phone_number: str


class ProfileOut(BaseModel):
    full_name: str | None
    phone_number: str | None
    fpl_team_name: str | None
    fpl_manager_id: str | None
    total_gameweeks: int
    total_points: int
    best_rank: int | None
    total_winnings: Decimal
    total_withdrawals: Decimal
    available_balance: Decimal


@router.patch("/me/phone")
def update_phone(payload: PhoneUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.phone_number = payload.phone_number
    db.commit()
    return {"status": "updated"}


@router.get("/me/profile", response_model=ProfileOut)
def my_profile(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team = db.query(FPLTeam).filter(FPLTeam.user_id == current_user.id).first()
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()

    total_gameweeks = (
        db.query(func.count(CompetitionEntry.id))
        .filter(
            CompetitionEntry.user_id == current_user.id,
            CompetitionEntry.status == EntryStatus.CONFIRMED,
        )
        .scalar()
        or 0
    )

    best_rank = (
        db.query(func.min(ScoreSnapshot.rank))
        .join(CompetitionEntry, CompetitionEntry.id == ScoreSnapshot.competition_entry_id)
        .filter(CompetitionEntry.user_id == current_user.id, ScoreSnapshot.rank.isnot(None))
        .scalar()
    )

    # Sum each entry's LATEST snapshot points (not every snapshot — those
    # accumulate as bonus points settle across a live gameweek).
    entries = (
        db.query(CompetitionEntry)
        .filter(CompetitionEntry.user_id == current_user.id, CompetitionEntry.status == EntryStatus.CONFIRMED)
        .all()
    )
    total_points = 0
    for entry in entries:
        latest = (
            db.query(ScoreSnapshot)
            .filter(ScoreSnapshot.competition_entry_id == entry.id)
            .order_by(ScoreSnapshot.captured_at.desc())
            .first()
        )
        if latest:
            total_points += latest.points

    total_winnings = (
        db.query(func.coalesce(func.sum(Prize.amount), 0))
        .filter(Prize.user_id == current_user.id)
        .scalar()
    )

    total_withdrawals = Decimal("0")
    if wallet is not None:
        withdrawn = (
            db.query(func.coalesce(func.sum(-WalletTransaction.amount), 0))
            .filter(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.type == WalletTxnType.WITHDRAWAL,
            )
            .scalar()
        )
        total_withdrawals = withdrawn or Decimal("0")

    return ProfileOut(
        full_name=current_user.full_name,
        phone_number=current_user.phone_number,
        fpl_team_name=team.team_name if team else None,
        fpl_manager_id=team.manager_id if team else None,
        total_gameweeks=total_gameweeks,
        total_points=total_points,
        best_rank=best_rank,
        total_winnings=total_winnings or Decimal("0"),
        total_withdrawals=total_withdrawals,
        available_balance=wallet.available_balance if wallet else Decimal("0"),
    )
