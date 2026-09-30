import re
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_verified_user
from app.models import User, Wallet, WalletTxnType, Withdrawal, WithdrawalStatus, utcnow
from app.schemas import WithdrawalCreate, WithdrawalOut
from app.services.ledger import DuplicateTransactionError, InsufficientFundsError, debit_wallet
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/withdrawals", tags=["withdrawals"])
_IDEM = re.compile(r"^[A-Za-z0-9\-_]{16,64}$")


@router.post("", response_model=WithdrawalOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("withdraw", 5, 60))])
def request_withdrawal(payload: WithdrawalCreate, idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                       user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    """
    Idempotent: the client sends a fresh random Idempotency-Key per user action, so a double tap or
    a retry returns the first withdrawal instead of creating a second one.
    Funds move out of `available` into `pending` in the same transaction as the request row.
    """
    if not _IDEM.fullmatch(idempotency_key):
        raise HTTPException(422, "Idempotency-Key header (16-64 chars) is required")
    existing = db.query(Withdrawal).filter(Withdrawal.user_id == user.id,
                                           Withdrawal.idempotency_key == idempotency_key).first()
    if existing:
        return existing
    if payload.amount < Decimal(settings.min_withdrawal):
        raise HTTPException(422, f"Minimum withdrawal is {settings.min_withdrawal} ETB")

    today = db.query(func.coalesce(func.sum(Withdrawal.amount), 0)).filter(
        Withdrawal.user_id == user.id, Withdrawal.requested_at >= utcnow() - timedelta(hours=24),
        Withdrawal.status.in_((WithdrawalStatus.PENDING, WithdrawalStatus.PROCESSING, WithdrawalStatus.SUCCESS))).scalar()
    if Decimal(today or 0) + payload.amount > Decimal(settings.max_withdrawal_per_day):
        raise HTTPException(422, "This would exceed your 24-hour withdrawal limit")

    wd = Withdrawal(user_id=user.id, amount=payload.amount, destination_account=payload.destination_account,
                    idempotency_key=idempotency_key)
    db.add(wd)
    try:
        db.flush()
        debit_wallet(db, user_id=user.id, amount=payload.amount, txn_type=WalletTxnType.WITHDRAWAL,
                     reference_type="withdrawal", reference_id=str(wd.id),
                     idempotency_key=f"withdrawal:{wd.id}")
    except InsufficientFundsError:
        db.rollback()
        raise HTTPException(402, "Amount exceeds your available balance")
    except (DuplicateTransactionError, IntegrityError):
        db.rollback()
        raise HTTPException(409, "This request was already submitted")
    wallet = db.query(Wallet).filter(Wallet.user_id == user.id).one()
    wallet.pending_balance = wallet.pending_balance + payload.amount
    db.commit()
    return wd


@router.get("/mine", response_model=list[WithdrawalOut])
def my_withdrawals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Withdrawal).filter(Withdrawal.user_id == user.id) \
        .order_by(Withdrawal.requested_at.desc()).limit(100).all()
