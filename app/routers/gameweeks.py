import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    CompetitionEntry,
    EntryStatus,
    FPLTeam,
    Gameweek,
    GameweekStatus,
    ScoreSnapshot,
    User,
    WalletTxnType,
)
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


class LeaderboardRow(BaseModel):
    rank: int | None
    display_name: str
    points: int
    is_me: bool


def _to_out(gw: Gameweek, db: Session, current_user: User | None) -> GameweekOut:
    participants = (
        db.query(func.count(CompetitionEntry.id))
        .filter(CompetitionEntry.gameweek_id == gw.id, CompetitionEntry.status == EntryStatus.CONFIRMED)
        .scalar()
        or 0
    )
    my_status = None
    if current_user is not None:
        entry = (
            db.query(CompetitionEntry)
            .filter(CompetitionEntry.gameweek_id == gw.id, CompetitionEntry.user_id == current_user.id)
            .first()
        )
        my_status = entry.status.value if entry else None

    return GameweekOut(
        id=gw.id,
        gw_number=gw.gw_number,
        entry_fee=gw.entry_fee,
        registration_deadline=gw.registration_deadline,
        status=gw.status.value,
        participants=participants,
        prize_pool=gw.entry_fee * participants,
        my_entry_status=my_status,
    )


@router.get("/current", response_model=GameweekOut)
def current_gameweek(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    gw = (
        db.query(Gameweek)
        .filter(
            Gameweek.status.in_(
                [GameweekStatus.REGISTRATION_OPEN, GameweekStatus.REGISTRATION_LOCKED]
            )
        )
        .order_by(Gameweek.gw_number.desc())
        .first()
    )
    if gw is None:
        raise HTTPException(status_code=404, detail="No open gameweek right now")
    return _to_out(gw, db, current_user)


@router.post("/{gameweek_id}/join", response_model=GameweekOut)
def join_gameweek(
    gameweek_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    gw = db.query(Gameweek).filter(Gameweek.id == gameweek_id).with_for_update().first()
    if gw is None:
        raise HTTPException(status_code=404, detail="Gameweek not found")
    if gw.status != GameweekStatus.REGISTRATION_OPEN:
        raise HTTPException(status_code=400, detail="Registration is not open for this gameweek")
    if datetime.utcnow() > gw.registration_deadline:
        raise HTTPException(status_code=400, detail="Registration deadline has passed")

    team = db.query(FPLTeam).filter(FPLTeam.user_id == current_user.id, FPLTeam.verified.is_(True)).first()
    if team is None:
        raise HTTPException(
            status_code=400, detail="Confirm your FPL Manager ID before joining a gameweek"
        )

    existing = (
        db.query(CompetitionEntry)
        .filter(CompetitionEntry.gameweek_id == gw.id, CompetitionEntry.user_id == current_user.id)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="You already joined this gameweek")

    try:
        txn = debit_wallet(
            db,
            user_id=current_user.id,
            amount=gw.entry_fee,
            txn_type=WalletTxnType.ENTRY_FEE,
            reference_type="gameweek",
            reference_id=str(gw.id),
            idempotency_key=f"entry:{current_user.id}:{gw.id}",
        )
    except InsufficientFundsError:
        raise HTTPException(status_code=402, detail="Insufficient wallet balance for the entry fee")
    except DuplicateTransactionError:
        raise HTTPException(status_code=409, detail="Entry already processed")

    entry = CompetitionEntry(
        user_id=current_user.id,
        gameweek_id=gw.id,
        fpl_team_id=team.id,
        wallet_transaction_id=txn.id,
        status=EntryStatus.CONFIRMED,
    )
    db.add(entry)
    db.commit()
    return _to_out(gw, db, current_user)


@router.get("/{gameweek_id}/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(
    gameweek_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Latest snapshot per entry for this gameweek.
    entries = (
        db.query(CompetitionEntry)
        .filter(CompetitionEntry.gameweek_id == gameweek_id, CompetitionEntry.status == EntryStatus.CONFIRMED)
        .all()
    )
    rows: list[LeaderboardRow] = []
    for entry in entries:
        latest = (
            db.query(ScoreSnapshot)
            .filter(ScoreSnapshot.competition_entry_id == entry.id)
            .order_by(ScoreSnapshot.captured_at.desc())
            .first()
        )
        if latest is None:
            continue
        team = db.query(FPLTeam).filter(FPLTeam.id == entry.fpl_team_id).first()
        rows.append(
            LeaderboardRow(
                rank=latest.rank,
                display_name=team.team_name if team else "Unknown",
                points=latest.points,
                is_me=(entry.user_id == current_user.id),
            )
        )
    rows.sort(key=lambda r: r.points, reverse=True)
    return rows
