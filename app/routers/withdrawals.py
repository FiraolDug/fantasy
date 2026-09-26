import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_finance_admin
from app.models import User, Wallet, WalletTxnType, Withdrawal, WithdrawalStatus
from app.services.ledger import DuplicateTransactionError, InsufficientFundsError, credit_wallet, debit_wallet
from app.utils.audit import log_action

router = APIRouter(prefix="/withdrawals", tags=["withdrawals"])


class WithdrawalCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    destination_account: str = Field(min_length=3, max_length=128)


class WithdrawalOut(BaseModel):
    id: uuid.UUID
    amount: Decimal
    destination_account: str
    status: str
    requested_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=WithdrawalOut, status_code=status.HTTP_201_CREATED)
def request_withdrawal(
    payload: WithdrawalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Deducts from available balance immediately (moved to pending) so the
    same funds can't be spent twice while a request is under review —
    per the v2 spec's withdrawal design. If rejected, the funds are
    credited back (see reject_withdrawal below).
    """
    try:
        debit_wallet(
            db,
            user_id=current_user.id,
            amount=payload.amount,
            txn_type=WalletTxnType.WITHDRAWAL,
            reference_type="withdrawal",
            reference_id=None,  # filled in after the Withdrawal row exists (see below)
            idempotency_key=f"withdrawal-request:{current_user.id}:{datetime.utcnow().timestamp()}",
        )
    except InsufficientFundsError:
        raise HTTPException(status_code=402, detail="Amount exceeds available balance")

    withdrawal = Withdrawal(
        user_id=current_user.id,
        amount=payload.amount,
        destination_account=payload.destination_account,
        status=WithdrawalStatus.PENDING,
    )
    db.add(withdrawal)
    db.flush()

    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    wallet.pending_balance = wallet.pending_balance + payload.amount

    # Backfill the ledger row's reference_id now that we have the Withdrawal's id,
    # so /wallet/transactions can show the withdrawal's real review status.
    from app.models import WalletTransaction

    txn = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.wallet_id == wallet.id, WalletTransaction.type == WalletTxnType.WITHDRAWAL)
        .order_by(WalletTransaction.created_at.desc())
        .first()
    )
    if txn:
        txn.reference_id = str(withdrawal.id)

    db.commit()
    db.refresh(withdrawal)
    return withdrawal


@router.get("/mine", response_model=list[WithdrawalOut])
def my_withdrawals(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Withdrawal)
        .filter(Withdrawal.user_id == current_user.id)
        .order_by(Withdrawal.requested_at.desc())
        .all()
    )


@router.get("", response_model=list[WithdrawalOut])
def list_withdrawals(admin: User = Depends(require_finance_admin), db: Session = Depends(get_db)):
    return db.query(Withdrawal).order_by(Withdrawal.requested_at.desc()).limit(200).all()


@router.post("/{withdrawal_id}/approve", response_model=WithdrawalOut)
def approve_withdrawal(
    withdrawal_id: uuid.UUID,
    admin: User = Depends(require_finance_admin),
    db: Session = Depends(get_db),
):
    """
    Marks as paid. Does NOT call a real payment provider (none is
    configured) — this records that YOU sent the money manually, the
    same way deposit verification works.
    """
    wd = db.query(Withdrawal).filter(Withdrawal.id == withdrawal_id).first()
    if wd is None:
        raise HTTPException(status_code=404, detail="Withdrawal not found")
    if wd.status != WithdrawalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Already {wd.status.value}")

    wallet = db.query(Wallet).filter(Wallet.user_id == wd.user_id).first()
    wallet.pending_balance = wallet.pending_balance - wd.amount

    before = {"status": wd.status.value}
    wd.status = WithdrawalStatus.SUCCESS
    wd.processed_at = datetime.utcnow()
    wd.processed_by = admin.id

    log_action(
        db,
        actor_id=admin.id,
        action="approve_withdrawal",
        entity_type="Withdrawal",
        entity_id=str(wd.id),
        before_state=before,
        after_state={"status": wd.status.value},
    )
    db.commit()
    db.refresh(wd)
    return wd


@router.post("/{withdrawal_id}/reject", response_model=WithdrawalOut)
def reject_withdrawal(
    withdrawal_id: uuid.UUID,
    reason: str,
    admin: User = Depends(require_finance_admin),
    db: Session = Depends(get_db),
):
    """Refunds the held funds back to available balance."""
    wd = db.query(Withdrawal).filter(Withdrawal.id == withdrawal_id).first()
    if wd is None:
        raise HTTPException(status_code=404, detail="Withdrawal not found")
    if wd.status != WithdrawalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Already {wd.status.value}")

    wallet = db.query(Wallet).filter(Wallet.user_id == wd.user_id).first()
    wallet.pending_balance = wallet.pending_balance - wd.amount

    try:
        credit_wallet(
            db,
            user_id=wd.user_id,
            amount=wd.amount,
            txn_type=WalletTxnType.REFUND,
            reference_type="withdrawal",
            reference_id=str(wd.id),
            idempotency_key=f"withdrawal-refund:{wd.id}",
        )
    except DuplicateTransactionError:
        pass  # already refunded — don't double-credit

    before = {"status": wd.status.value}
    wd.status = WithdrawalStatus.FAILED
    wd.processed_at = datetime.utcnow()
    wd.processed_by = admin.id

    log_action(
        db,
        actor_id=admin.id,
        action="reject_withdrawal",
        entity_type="Withdrawal",
        entity_id=str(wd.id),
        before_state=before,
        after_state={"status": wd.status.value, "reason": reason},
    )
    db.commit()
    db.refresh(wd)
    return wd
