"""
JSON API for the branded admin SPA (admin/index.html). Uses the same
JWT auth as everything else (POST /auth/login), role-gated per endpoint
via app/deps.py — separate from the sqladmin CRUD panel at /admin,
which still exists for anything not covered here (full record editing).
"""
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_finance_admin
from app.models import (
    AuditLog,
    CompetitionEntry,
    DepositRequest,
    DepositStatus,
    EntryStatus,
    FPLTeam,
    FraudAlert,
    Gameweek,
    GameweekConfig,
    GameweekStatus,
    User,
    UserRole,
    Wallet,
    Withdrawal,
    WithdrawalStatus,
)
from app.services.settings import get_platform_settings

router = APIRouter(prefix="/admin-api", tags=["admin-api"])


# ---------- Overview ----------

@router.get("/overview")
def overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    current_gw = (
        db.query(Gameweek)
        .filter(Gameweek.status.in_([GameweekStatus.REGISTRATION_OPEN, GameweekStatus.REGISTRATION_LOCKED]))
        .order_by(Gameweek.gw_number.desc())
        .first()
    )
    participants = 0
    prize_pool = Decimal("0")
    if current_gw is not None:
        participants = (
            db.query(func.count(CompetitionEntry.id))
            .filter(CompetitionEntry.gameweek_id == current_gw.id, CompetitionEntry.status == EntryStatus.CONFIRMED)
            .scalar()
            or 0
        )
        prize_pool = current_gw.entry_fee * participants

    pending_deposits = db.query(func.count(DepositRequest.id)).filter(DepositRequest.status == DepositStatus.PENDING).scalar() or 0
    pending_deposit_amount = db.query(func.coalesce(func.sum(DepositRequest.amount), 0)).filter(DepositRequest.status == DepositStatus.PENDING).scalar() or 0
    pending_withdrawals = db.query(func.count(Withdrawal.id)).filter(Withdrawal.status == WithdrawalStatus.PENDING).scalar() or 0
    pending_withdrawal_amount = db.query(func.coalesce(func.sum(Withdrawal.amount), 0)).filter(Withdrawal.status == WithdrawalStatus.PENDING).scalar() or 0
    completed_payout_amount = db.query(func.coalesce(func.sum(Withdrawal.amount), 0)).filter(Withdrawal.status == WithdrawalStatus.SUCCESS).scalar() or 0
    open_fraud_alerts = db.query(func.count(FraudAlert.id)).filter(FraudAlert.resolved.is_(False)).scalar() or 0
    total_users = db.query(func.count(User.id)).filter(User.role == UserRole.USER).scalar() or 0

    return {
        "current_gw_number": current_gw.gw_number if current_gw else None,
        "participants": participants,
        "prize_pool": str(prize_pool),
        "pending_deposits": pending_deposits,
        "pending_deposit_amount": str(pending_deposit_amount),
        "pending_withdrawals": pending_withdrawals,
        "pending_withdrawal_amount": str(pending_withdrawal_amount),
        "completed_payout_amount": str(completed_payout_amount),
        "open_fraud_alerts": open_fraud_alerts,
        "total_users": total_users,
    }


# ---------- Users (read-only — role changes stay in the sqladmin panel for now) ----------

@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).filter(User.role == UserRole.USER).order_by(User.created_at.desc()).limit(200).all()
    out = []
    for u in users:
        team = db.query(FPLTeam).filter(FPLTeam.user_id == u.id).first()
        wallet = db.query(Wallet).filter(Wallet.user_id == u.id).first()
        out.append(
            {
                "id": str(u.id),
                "full_name": u.full_name,
                "phone_number": u.phone_number,
                "fpl_team_name": team.team_name if team else None,
                "is_active": u.is_active,
                "available_balance": str(wallet.available_balance) if wallet else "0",
                "created_at": u.created_at.isoformat(),
            }
        )
    return out


# ---------- Gameweeks ----------

class GameweekCreate(BaseModel):
    gw_number: int
    entry_fee: Decimal = Decimal("200")
    registration_deadline: datetime


@router.get("/gameweeks")
def list_gameweeks(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    gws = db.query(Gameweek).order_by(Gameweek.gw_number.desc()).all()
    out = []
    for gw in gws:
        participants = (
            db.query(func.count(CompetitionEntry.id))
            .filter(CompetitionEntry.gameweek_id == gw.id, CompetitionEntry.status == EntryStatus.CONFIRMED)
            .scalar()
            or 0
        )
        out.append(
            {
                "id": str(gw.id),
                "gw_number": gw.gw_number,
                "entry_fee": str(gw.entry_fee),
                "registration_deadline": gw.registration_deadline.isoformat(),
                "status": gw.status.value,
                "participants": participants,
                "prize_pool": str(gw.entry_fee * participants),
            }
        )
    return out


@router.post("/gameweeks", status_code=status.HTTP_201_CREATED)
def create_gameweek(payload: GameweekCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.query(Gameweek).filter(Gameweek.gw_number == payload.gw_number).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Gameweek {payload.gw_number} already exists")

    gw = Gameweek(
        gw_number=payload.gw_number,
        entry_fee=payload.entry_fee,
        registration_deadline=payload.registration_deadline,
        status=GameweekStatus.REGISTRATION_OPEN,
    )
    db.add(gw)
    db.flush()
    db.add(GameweekConfig(gameweek_id=gw.id))
    db.commit()
    return {"id": str(gw.id), "gw_number": gw.gw_number}


# ---------- Fraud alerts ----------

@router.get("/fraud-alerts")
def list_fraud_alerts(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    alerts = db.query(FraudAlert).order_by(FraudAlert.created_at.desc()).limit(200).all()
    return [
        {
            "id": str(a.id),
            "category": a.category,
            "description": a.description,
            "resolved": a.resolved,
            "created_at": a.created_at.isoformat(),
        }
        for a in alerts
    ]


@router.post("/fraud-alerts/{alert_id}/resolve")
def resolve_fraud_alert(alert_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    alert = db.query(FraudAlert).filter(FraudAlert.id == alert_id).first()
    if alert is None:
        raise HTTPException(status_code=404, detail="Fraud alert not found")
    alert.resolved = True
    db.commit()
    return {"status": "resolved"}


# ---------- Audit log (read-only, append-only by design) ----------

@router.get("/audit-log")
def list_audit_log(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    return [
        {
            "id": str(l.id),
            "actor_id": str(l.actor_id) if l.actor_id else None,
            "action": l.action,
            "entity_type": l.entity_type,
            "entity_id": l.entity_id,
            "created_at": l.created_at.isoformat(),
        }
        for l in logs
    ]


# ---------- Platform settings (super-admin only to change; any admin to view) ----------

class PlatformSettingsUpdate(BaseModel):
    ads_enabled: bool


@router.get("/settings")
def get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = get_platform_settings(db)
    return {"ads_enabled": row.ads_enabled}


@router.patch("/settings")
def update_settings(
    payload: PlatformSettingsUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if admin.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a super admin can change platform-wide settings.",
        )
    row = get_platform_settings(db)
    row.ads_enabled = payload.ads_enabled
    db.commit()
    db.refresh(row)
    return {"ads_enabled": row.ads_enabled}
