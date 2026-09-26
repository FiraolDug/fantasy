"""
A single stats overview page (spec section 17) that sqladmin's generic
CRUD views don't give you out of the box. Reuses the same session cookie
sqladmin's AdminAuth sets, so logging in once covers both.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from app.database import SessionLocal
from app.models import (
    CompetitionEntry,
    DepositRequest,
    DepositStatus,
    EntryStatus,
    FraudAlert,
    Gameweek,
    GameweekStatus,
    Withdrawal,
    WithdrawalStatus,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if "admin_user_id" not in request.session:
        return RedirectResponse(url="/admin/login")

    db = SessionLocal()
    try:
        current_gw = (
            db.query(Gameweek)
            .filter(
                Gameweek.status.in_(
                    [GameweekStatus.REGISTRATION_OPEN, GameweekStatus.REGISTRATION_LOCKED]
                )
            )
            .order_by(Gameweek.gw_number.desc())
            .first()
        )

        participants = 0
        prize_pool = Decimal("0")
        if current_gw is not None:
            participants = (
                db.query(func.count(CompetitionEntry.id))
                .filter(
                    CompetitionEntry.gameweek_id == current_gw.id,
                    CompetitionEntry.status == EntryStatus.CONFIRMED,
                )
                .scalar()
                or 0
            )
            prize_pool = current_gw.entry_fee * participants

        pending_deposits = (
            db.query(func.count(DepositRequest.id))
            .filter(DepositRequest.status == DepositStatus.PENDING)
            .scalar()
            or 0
        )
        pending_deposit_amount = (
            db.query(func.coalesce(func.sum(DepositRequest.amount), 0))
            .filter(DepositRequest.status == DepositStatus.PENDING)
            .scalar()
            or 0
        )
        pending_withdrawals = (
            db.query(func.count(Withdrawal.id))
            .filter(Withdrawal.status == WithdrawalStatus.PENDING)
            .scalar()
            or 0
        )
        completed_payout_amount = (
            db.query(func.coalesce(func.sum(Withdrawal.amount), 0))
            .filter(Withdrawal.status == WithdrawalStatus.SUCCESS)
            .scalar()
            or 0
        )
        open_fraud_alerts = (
            db.query(func.count(FraudAlert.id)).filter(FraudAlert.resolved.is_(False)).scalar() or 0
        )
        recent_deposits = (
            db.query(DepositRequest).order_by(DepositRequest.created_at.desc()).limit(10).all()
        )

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "current_gw": current_gw,
                "participants": participants,
                "prize_pool": prize_pool,
                "pending_deposits": pending_deposits,
                "pending_deposit_amount": pending_deposit_amount,
                "pending_withdrawals": pending_withdrawals,
                "completed_payout_amount": completed_payout_amount,
                "open_fraud_alerts": open_fraud_alerts,
                "recent_deposits": recent_deposits,
                "now": datetime.utcnow(),
            },
        )
    finally:
        db.close()
