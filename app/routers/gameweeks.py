import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_verified_user
from app.models import (
    CompetitionEntry, EntryStatus, Gameweek, GameweekStatus, Prize, ScoreSnapshot, User, WalletTxnType, utcnow,
)
from app.services.referrals import reward_if_due
from app.services.ledger import DuplicateTransactionError, InsufficientFundsError, debit_wallet

router = APIRouter(prefix="/gameweeks", tags=["gameweeks"])


class GameweekOut(BaseModel):
    id: uuid.UUID
    gw_number: int
    entry_fee: Decimal
    registration_deadline: datetime
    status: str
    participants: int
    prize_pool: Decimal
    my_entry_status: str | None = None


class StandingRow(BaseModel):
    rank: int | None
    display_name: str
    points: int
    is_me: bool = True


def _participants(db: Session, gw_id) -> int:
    return db.query(func.count(CompetitionEntry.id)).filter(
        CompetitionEntry.gameweek_id == gw_id, CompetitionEntry.status == EntryStatus.CONFIRMED).scalar() or 0


def _to_out(gw: Gameweek, db: Session, user: User) -> GameweekOut:
    entry = db.query(CompetitionEntry).filter(
        CompetitionEntry.gameweek_id == gw.id, CompetitionEntry.user_id == user.id).first()
    n = _participants(db, gw.id)
    return GameweekOut(id=gw.id, gw_number=gw.gw_number, entry_fee=gw.entry_fee,
                       registration_deadline=gw.registration_deadline, status=gw.status.value,
                       participants=n, prize_pool=gw.entry_fee * n,
                       my_entry_status=entry.status.value if entry else None)


@router.get("/history", response_model=list[dict])
def gameweek_history(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Aggregate results only. No team names or user identifiers of other people."""
    out = []
    for gw in db.query(Gameweek).filter(Gameweek.status == GameweekStatus.ARCHIVED) \
            .order_by(Gameweek.gw_number.desc()).limit(20):
        n = _participants(db, gw.id)
        top = db.query(Prize).filter(Prize.gameweek_id == gw.id, Prize.position == 1).first()
        out.append({"gw_number": gw.gw_number, "participants": n, "prize_pool": str(gw.entry_fee * n),
                    "winner_team_name": None, "winner_prize": str(top.amount) if top else None})
    return out


@router.get("/current", response_model=GameweekOut)
def current_gameweek(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    gw = db.query(Gameweek).filter(Gameweek.status.in_(
        [GameweekStatus.REGISTRATION_OPEN, GameweekStatus.REGISTRATION_LOCKED])) \
        .order_by(Gameweek.gw_number.desc()).first()
    if gw is None:
        raise HTTPException(404, "No open gameweek right now")
    return _to_out(gw, db, user)


@router.post("/{gameweek_id}/join", response_model=GameweekOut)
def join_gameweek(gameweek_id: uuid.UUID, user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    gw = db.query(Gameweek).filter(Gameweek.id == gameweek_id).with_for_update().first()
    if gw is None:
        raise HTTPException(404, "Gameweek not found")
    if gw.status != GameweekStatus.REGISTRATION_OPEN or utcnow() > gw.registration_deadline:
        raise HTTPException(400, "Registration is closed for this gameweek")
    if db.query(CompetitionEntry.id).filter(
            CompetitionEntry.gameweek_id == gw.id, CompetitionEntry.user_id == user.id).first():
        raise HTTPException(409, "You already joined this gameweek")
    try:
        txn = debit_wallet(db, user_id=user.id, amount=gw.entry_fee, txn_type=WalletTxnType.ENTRY_FEE,
                           reference_type="gameweek", reference_id=str(gw.id),
                           idempotency_key=f"entry:{user.id}:{gw.id}")
    except InsufficientFundsError:
        raise HTTPException(402, "Insufficient wallet balance for the entry fee")
    except DuplicateTransactionError:
        raise HTTPException(409, "Entry already processed")
    db.add(CompetitionEntry(user_id=user.id, gameweek_id=gw.id, fpl_team_id=user.fpl_team.id,
                            wallet_transaction_id=txn.id, status=EntryStatus.CONFIRMED))
    db.flush()
    reward_if_due(db, None, user, "first_entry")     # same transaction as the entry fee
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "You already joined this gameweek")
    return _to_out(gw, db, user)


@router.get("/{gameweek_id}/leaderboard", response_model=list[StandingRow])
def my_standing(gameweek_id: uuid.UUID, user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    """
    Returns the caller's own standing only. Other participants' teams, names and scores are never
    sent to the client, so nothing can be scraped from this endpoint.
    """
    entry = db.query(CompetitionEntry).filter(
        CompetitionEntry.gameweek_id == gameweek_id, CompetitionEntry.user_id == user.id,
        CompetitionEntry.status == EntryStatus.CONFIRMED).first()
    if entry is None:
        return []
    latest = db.query(ScoreSnapshot).filter(ScoreSnapshot.competition_entry_id == entry.id) \
        .order_by(ScoreSnapshot.captured_at.desc()).first()
    if latest is None:
        return []
    return [StandingRow(rank=latest.rank, display_name=user.fpl_team.team_name, points=latest.points)]
