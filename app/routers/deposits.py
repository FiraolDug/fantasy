from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_verified_user
from app.models import DepositMethod, DepositRequest, DepositStatus, User
from app.schemas import DepositInstructions, DepositRequestCreate, DepositRequestOut
from app.services.audit import security_event
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/deposits", tags=["deposits"])


@router.get("/instructions", response_model=DepositInstructions)
def deposit_instructions(_: User = Depends(require_verified_user)):
    return DepositInstructions(
        telebirr_receiver_name=settings.telebirr_receiver_name,
        telebirr_receiver_number=settings.telebirr_receiver_number,
        cbe_receiver_name=settings.cbe_receiver_name,
        cbe_account_number=settings.cbe_account_number,
        instructions=("Send the amount to the account above, then enter the transaction ID from your receipt. "
                      "Your wallet is credited after we confirm the payment."),
    )


@router.post("", response_model=DepositRequestOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit("deposit", 10, 60))])
def create_deposit_request(payload: DepositRequestCreate, request: Request,
                           user: User = Depends(require_verified_user), db: Session = Depends(get_db)):
    if not (Decimal(settings.min_deposit) <= payload.amount <= Decimal(settings.max_deposit)):
        raise HTTPException(422, f"Deposits must be between {settings.min_deposit} and {settings.max_deposit} ETB")
    receiver = settings.telebirr_receiver_number if payload.method == DepositMethod.TELEBIRR else settings.cbe_account_number
    deposit = DepositRequest(user_id=user.id, method=payload.method, receiver_account=receiver,
                             amount=payload.amount, transaction_id=payload.transaction_id)
    db.add(deposit)
    try:
        db.commit()
    except IntegrityError:     # UNIQUE(method, transaction_id): one receipt can only ever be claimed once
        db.rollback()
        security_event(db, request, "duplicate_deposit_txid", user_id=user.id)
        db.commit()
        raise HTTPException(409, "This transaction ID has already been submitted.")
    return deposit


@router.get("/mine", response_model=list[DepositRequestOut])
def my_deposits(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(DepositRequest).filter(DepositRequest.user_id == user.id) \
        .order_by(DepositRequest.created_at.desc()).limit(100).all()
