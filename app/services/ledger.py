"""
Wallet ledger service.

All balance changes MUST go through credit_wallet / debit_wallet so that:
  - every change produces a WalletTransaction row (the audit trail)
  - balances are only ever moved inside a DB transaction
  - idempotency_key prevents a retried webhook / admin double-click from
    double-crediting or double-debiting a wallet
"""
import uuid
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Wallet, WalletTransaction, WalletTxnStatus, WalletTxnType


class InsufficientFundsError(Exception):
    pass


class DuplicateTransactionError(Exception):
    pass


def _get_or_create_wallet(db: Session, user_id: uuid.UUID) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.user_id == user_id).with_for_update().first()
    if wallet is None:
        wallet = Wallet(user_id=user_id, available_balance=Decimal("0"), pending_balance=Decimal("0"))
        db.add(wallet)
        db.flush()
    return wallet


def credit_wallet(
    db: Session,
    *,
    user_id: uuid.UUID,
    amount: Decimal,
    txn_type: WalletTxnType,
    reference_type: str | None = None,
    reference_id: str | None = None,
    idempotency_key: str | None = None,
) -> WalletTransaction:
    if amount <= 0:
        raise ValueError("credit amount must be positive")

    wallet = _get_or_create_wallet(db, user_id)
    wallet.available_balance = wallet.available_balance + amount

    txn = WalletTransaction(
        wallet_id=wallet.id,
        type=txn_type,
        amount=amount,
        balance_after=wallet.available_balance,
        status=WalletTxnStatus.SUCCESS,
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
    )
    db.add(txn)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateTransactionError(
            f"idempotency_key {idempotency_key!r} already used"
        ) from exc
    return txn


def debit_wallet(
    db: Session,
    *,
    user_id: uuid.UUID,
    amount: Decimal,
    txn_type: WalletTxnType,
    reference_type: str | None = None,
    reference_id: str | None = None,
    idempotency_key: str | None = None,
    allow_negative: bool = False,
) -> WalletTransaction:
    if amount <= 0:
        raise ValueError("debit amount must be positive")

    wallet = _get_or_create_wallet(db, user_id)
    if not allow_negative and wallet.available_balance < amount:
        raise InsufficientFundsError(
            f"wallet {wallet.id} has {wallet.available_balance}, needs {amount}"
        )

    wallet.available_balance = wallet.available_balance - amount

    txn = WalletTransaction(
        wallet_id=wallet.id,
        type=txn_type,
        amount=-amount,
        balance_after=wallet.available_balance,
        status=WalletTxnStatus.SUCCESS,
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
    )
    db.add(txn)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateTransactionError(
            f"idempotency_key {idempotency_key!r} already used"
        ) from exc
    return txn
