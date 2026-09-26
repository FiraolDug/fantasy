import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_finance_admin
from app.models import (
    DepositRequest,
    DepositStatus,
    User,
    WalletTxnType,
)
from app.schemas import DepositInstructions, DepositRequestCreate, DepositRequestOut
from app.services.ledger import DuplicateTransactionError, credit_wallet
from app.utils.audit import log_action

router = APIRouter(prefix="/deposits", tags=["deposits"])


@router.get("/instructions", response_model=DepositInstructions)
def deposit_instructions():
    """
    Static manual-deposit destinations. The user pays to one of these
    accounts outside the platform, then submits the transaction ID
    via POST /deposits for admin verification.
    """
    return DepositInstructions(
        telebirr_receiver_name=settings.telebirr_receiver_name,
        telebirr_receiver_number=settings.telebirr_receiver_number,
        cbe_receiver_name=settings.cbe_receiver_name,
        cbe_account_number=settings.cbe_account_number,
        instructions=(
            "Send the exact entry/deposit amount to the account above, then submit "
            "the transaction ID / reference code you received here. Your wallet is "
            "credited only after an admin verifies the payment."
        ),
    )


@router.post("", response_model=DepositRequestOut, status_code=status.HTTP_201_CREATED)
def create_deposit_request(
    payload: DepositRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    receiver_account = (
        settings.telebirr_receiver_number
        if payload.method.value == "telebirr"
        else settings.cbe_account_number
    )

    if settings.enforce_unique_deposit_txn_id:
        existing = (
            db.query(DepositRequest)
            .filter(DepositRequest.transaction_id == payload.transaction_id)
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This transaction ID has already been submitted.",
            )
    # NOTE: duplicate-transaction-ID checking is OFF by default
    # (ENFORCE_UNIQUE_DEPOSIT_TXN_ID=false) for MVP/test use, as requested.
    # Turn it on in .env before accepting real-money deposits.

    deposit = DepositRequest(
        user_id=current_user.id,
        method=payload.method,
        receiver_account=receiver_account,
        amount=payload.amount,
        transaction_id=payload.transaction_id,
        status=DepositStatus.PENDING,
    )
    db.add(deposit)
    db.commit()
    db.refresh(deposit)
    return deposit


@router.get("/mine", response_model=list[DepositRequestOut])
def my_deposits(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(DepositRequest)
        .filter(DepositRequest.user_id == current_user.id)
        .order_by(DepositRequest.created_at.desc())
        .all()
    )


@router.post("/{deposit_id}/approve", response_model=DepositRequestOut)
def approve_deposit(
    deposit_id: uuid.UUID,
    admin: User = Depends(require_finance_admin),
    db: Session = Depends(get_db),
):
    """
    Finance-admin-only. Verifies the transaction_id against the bank/Telebirr
    statement manually, then credits the user's wallet through the ledger
    (never by editing Wallet.available_balance directly).
    """
    deposit = db.query(DepositRequest).filter(DepositRequest.id == deposit_id).first()
    if deposit is None:
        raise HTTPException(status_code=404, detail="Deposit request not found")
    if deposit.status != DepositStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Deposit already {deposit.status.value}")

    try:
        credit_wallet(
            db,
            user_id=deposit.user_id,
            amount=deposit.amount,
            txn_type=WalletTxnType.DEPOSIT,
            reference_type="deposit_request",
            reference_id=str(deposit.id),
            idempotency_key=f"deposit:{deposit.id}",
        )
    except DuplicateTransactionError:
        raise HTTPException(status_code=409, detail="This deposit was already credited.")

    before = {"status": deposit.status.value}
    deposit.status = DepositStatus.APPROVED
    deposit.reviewed_by = admin.id
    from datetime import datetime

    deposit.reviewed_at = datetime.utcnow()

    log_action(
        db,
        actor_id=admin.id,
        action="approve_deposit",
        entity_type="DepositRequest",
        entity_id=str(deposit.id),
        before_state=before,
        after_state={"status": deposit.status.value, "amount": str(deposit.amount)},
    )

    db.commit()
    db.refresh(deposit)
    return deposit


@router.post("/{deposit_id}/reject", response_model=DepositRequestOut)
def reject_deposit(
    deposit_id: uuid.UUID,
    reason: str,
    admin: User = Depends(require_finance_admin),
    db: Session = Depends(get_db),
):
    deposit = db.query(DepositRequest).filter(DepositRequest.id == deposit_id).first()
    if deposit is None:
        raise HTTPException(status_code=404, detail="Deposit request not found")
    if deposit.status != DepositStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Deposit already {deposit.status.value}")

    before = {"status": deposit.status.value}
    deposit.status = DepositStatus.REJECTED
    deposit.reviewed_by = admin.id
    deposit.note = reason
    from datetime import datetime

    deposit.reviewed_at = datetime.utcnow()

    log_action(
        db,
        actor_id=admin.id,
        action="reject_deposit",
        entity_type="DepositRequest",
        entity_id=str(deposit.id),
        before_state=before,
        after_state={"status": deposit.status.value, "reason": reason},
    )

    db.commit()
    db.refresh(deposit)
    return deposit
