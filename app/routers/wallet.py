from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    DepositRequest,
    User,
    Wallet,
    WalletTransaction,
    WalletTxnType,
    Withdrawal,
)
from app.schemas import WalletOut

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/me", response_model=WalletOut)
def my_wallet(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    if wallet is None:
        wallet = Wallet(user_id=current_user.id)
        db.add(wallet)
        db.commit()
        db.refresh(wallet)
    return wallet


class TransactionOut(BaseModel):
    id: str
    type: str
    amount: str
    status: str  # reflects the *real* lifecycle status (deposit/withdrawal review), not just the ledger write
    method: str | None = None
    created_at: str


@router.get("/transactions", response_model=list[TransactionOut])
def my_transactions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Powers the Mini App's transaction history. A wallet ledger entry is
    always written as SUCCESS the moment it's created (it's an atomic,
    final debit/credit) — but for deposits and withdrawals the *review*
    can still be pending/fail afterward, so we look up the linked
    DepositRequest/Withdrawal for the status a user actually cares about.
    """
    wallet = db.query(Wallet).filter(Wallet.user_id == current_user.id).first()
    if wallet is None:
        return []

    txns = (
        db.query(WalletTransaction)
        .filter(WalletTransaction.wallet_id == wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(100)
        .all()
    )

    out: list[TransactionOut] = []
    for t in txns:
        status = t.status.value
        method = None
        if t.type == WalletTxnType.DEPOSIT and t.reference_id:
            dep = db.query(DepositRequest).filter(DepositRequest.id == t.reference_id).first()
            if dep:
                status = dep.status.value
                method = dep.method.value
        elif t.type == WalletTxnType.WITHDRAWAL and t.reference_id:
            wd = db.query(Withdrawal).filter(Withdrawal.id == t.reference_id).first()
            if wd:
                status = wd.status.value

        out.append(
            TransactionOut(
                id=str(t.id),
                type=t.type.value,
                amount=str(t.amount),
                status=status,
                method=method,
                created_at=t.created_at.isoformat(),
            )
        )
    return out
