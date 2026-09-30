"""
Admin JSON API (cookie session + CSRF). Every route names the permission it needs, every change
writes an AuditLog row in the same transaction, and money moves only through the ledger service.
"""
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AdminContext, get_admin, require_permission as need
from app.models import (
    AdminAccount, AdminSession, AdminStatus, AuditLog, CompetitionEntry, DepositRequest, DepositStatus, FplTeam,
    FplVerification, FplVerificationAttempt, FraudAlert, Gameweek, GameweekConfig, GameweekStatus, Post, PostStatus,
    Referral, ReferralStatus, Role, SecurityEvent, User, UserSession, UserStatus, Wallet, WalletTxnType, Withdrawal, WithdrawalStatus, utcnow,
)
from app.security import hash_password, new_totp_secret, password_is_strong, totp_uri
from app.services.audit import audit
from app.services import referrals as referral_svc
from app.services.ledger import DuplicateTransactionError, credit_wallet
from app.services.settings import get_platform_settings

router = APIRouter(prefix="/admin-api", tags=["admin-api"])


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Reason(In):
    reason: str = Field(min_length=5, max_length=300)


def _mask_phone(p: str | None) -> str | None:
    return None if not p else p[:4] + "•" * max(0, len(p) - 7) + p[-3:]


def _get_or_404(db: Session, model, id_: uuid.UUID, what: str):
    row = db.get(model, id_)
    if row is None:
        raise HTTPException(404, f"{what} not found")
    return row


# =============================================================== overview
@router.get("/overview")
def overview(ctx: AdminContext = Depends(get_admin), db: Session = Depends(get_db)):
    q = lambda m, *f: db.query(func.count(m.id)).filter(*f).scalar() or 0  # noqa: E731
    out = {
        "users": q(User), "verified_users": q(FplTeam),
        "open_fraud_alerts": q(FraudAlert, FraudAlert.resolved.is_(False)) if ctx.has("fraud.manage") else None,
        "pending_deposits": q(DepositRequest, DepositRequest.status == DepositStatus.PENDING) if ctx.has("deposit.review") else None,
        "pending_withdrawals": q(Withdrawal, Withdrawal.status == WithdrawalStatus.PENDING) if ctx.has("withdrawal.review") else None,
        "draft_posts": q(Post, Post.status == PostStatus.DRAFT, Post.deleted_at.is_(None)) if ctx.has("post.read") else None,
    }
    gw = db.query(Gameweek).filter(Gameweek.status.in_([GameweekStatus.REGISTRATION_OPEN, GameweekStatus.REGISTRATION_LOCKED])) \
        .order_by(Gameweek.gw_number.desc()).first()
    out["current_gameweek"] = gw.gw_number if gw else None
    return out


# =============================================================== users & verification
@router.get("/users")
def list_users(q: str = "", ctx: AdminContext = Depends(need("user.read")), db: Session = Depends(get_db)):
    query = db.query(User, FplTeam).outerjoin(FplTeam, FplTeam.user_id == User.id)
    q = q.strip()[:64]
    if q:
        query = query.filter((User.telegram_id == q) | (FplTeam.manager_id == q) | User.full_name.like(q + "%"))
    return [{"id": str(u.id), "full_name": u.full_name, "phone": _mask_phone(u.phone_number), "status": u.status.value,
             "phone_verified": u.is_phone_verified, "team_name": t.team_name if t else None,
             "manager_id": t.manager_id if t else None, "created_at": u.created_at.isoformat() + "Z"}
            for u, t in query.order_by(User.created_at.desc()).limit(100)]


@router.get("/users/{user_id}")
def user_detail(user_id: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("user.read")),
                db: Session = Depends(get_db)):
    u = _get_or_404(db, User, user_id, "User")
    phone = u.phone_number if ctx.has("user.read_pii") else _mask_phone(u.phone_number)
    if ctx.has("user.read_pii"):
        audit(db, request, actor_admin_id=ctx.id, action="user.view_pii", entity_type="User", entity_id=u.id)
        db.commit()
    attempts = db.query(FplVerificationAttempt).filter(FplVerificationAttempt.user_id == u.id) \
        .order_by(FplVerificationAttempt.created_at.desc()).limit(20).all()
    alerts = db.query(FraudAlert).filter(FraudAlert.related_user_id == u.id).order_by(FraudAlert.created_at.desc()).limit(10).all()
    return {"id": str(u.id), "full_name": u.full_name, "telegram_id": u.telegram_id, "phone": phone,
            "status": u.status.value, "created_at": u.created_at.isoformat() + "Z",
            "team": {"manager_id": u.fpl_team.manager_id, "team_name": u.fpl_team.team_name,
                     "method": u.fpl_team.verification_method.value} if u.fpl_team else None,
            "attempts": [{"stage": a.stage.value, "outcome": a.outcome.value, "manager_id": a.manager_id,
                          "at": a.created_at.isoformat() + "Z"} for a in attempts],
            "alerts": [{"category": a.category, "severity": a.severity.value, "resolved": a.resolved} for a in alerts]}


class StatusIn(Reason):
    status: Literal["ACTIVE", "SUSPENDED", "BANNED"]


@router.post("/users/{user_id}/status")
def set_user_status(user_id: uuid.UUID, p: StatusIn, request: Request, ctx: AdminContext = Depends(need("user.suspend")),
                    db: Session = Depends(get_db)):
    u = _get_or_404(db, User, user_id, "User")
    before = {"status": u.status.value}
    u.status = UserStatus(p.status)
    if u.status != UserStatus.ACTIVE:
        for s in db.query(UserSession).filter(UserSession.user_id == u.id, UserSession.revoked_at.is_(None)):
            s.revoked_at = utcnow()
    audit(db, request, actor_admin_id=ctx.id, action="user.set_status", entity_type="User", entity_id=u.id,
          before=before, after={"status": u.status.value, "reason": p.reason})
    db.commit()
    return {"status": u.status.value}


@router.post("/users/{user_id}/unlink-team")
def unlink_team(user_id: uuid.UUID, p: Reason, request: Request,
                ctx: AdminContext = Depends(need("verification.review")), db: Session = Depends(get_db)):
    u = _get_or_404(db, User, user_id, "User")
    team = u.fpl_team
    if team is None:
        raise HTTPException(409, "This user has no linked team.")
    if db.query(CompetitionEntry.id).filter(CompetitionEntry.fpl_team_id == team.id).first():
        raise HTTPException(409, "This team has competition entries. Suspend the user instead.")
    before = {"manager_id": team.manager_id, "team_name": team.team_name}
    db.delete(team)
    audit(db, request, actor_admin_id=ctx.id, action="verification.unlink_team", entity_type="User", entity_id=u.id,
          before=before, after={"reason": p.reason})
    db.commit()
    return {"ok": True}


@router.get("/verifications")
def verifications(status: str = "", ctx: AdminContext = Depends(need("verification.review")), db: Session = Depends(get_db)):
    q = db.query(FplVerification, User).join(User, User.id == FplVerification.user_id)
    if status:
        q = q.filter(FplVerification.status == status.upper())
    return [{"id": str(v.id), "user_id": str(u.id), "user": u.full_name, "manager_id": v.manager_id,
             "status": v.status.value, "created_at": v.created_at.isoformat() + "Z"}
            for v, u in q.order_by(FplVerification.created_at.desc()).limit(100)]


# =============================================================== posts
def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return s or uuid.uuid4().hex[:8]


class PostIn(In):
    title: str = Field(min_length=3, max_length=160)
    summary: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    pinned: bool = False
    status: Literal["DRAFT", "PUBLISHED"] = "DRAFT"

    @field_validator("title", "summary", "body")
    @classmethod
    def _no_control(cls, v):
        if v is not None and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v):
            raise ValueError("Contains control characters")
        return v


class PostPatch(In):
    title: str | None = Field(default=None, min_length=3, max_length=160)
    summary: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=20000)
    pinned: bool | None = None
    status: Literal["DRAFT", "PUBLISHED", "ARCHIVED"] | None = None


def _post_out(p: Post) -> dict:
    return {"id": str(p.id), "title": p.title, "slug": p.slug, "summary": p.summary, "body": p.body, "pinned": p.pinned,
            "status": p.status.value, "published_at": p.published_at.isoformat() + "Z" if p.published_at else None,
            "updated_at": p.updated_at.isoformat() + "Z"}


@router.get("/posts")
def list_posts(ctx: AdminContext = Depends(need("post.read")), db: Session = Depends(get_db)):
    return [_post_out(p) for p in db.query(Post).filter(Post.deleted_at.is_(None)).order_by(Post.updated_at.desc()).limit(200)]


@router.post("/posts", status_code=201)
def create_post(p: PostIn, request: Request, ctx: AdminContext = Depends(need("post.write")), db: Session = Depends(get_db)):
    if p.status == "PUBLISHED" and not ctx.has("post.publish"):
        raise HTTPException(403, "You can save drafts, but publishing needs the publish permission.")
    slug, n = _slugify(p.title), 1
    while db.query(Post.id).filter(Post.slug == slug).first():
        n += 1
        slug = f"{_slugify(p.title)}-{n}"
    post = Post(author_id=ctx.id, title=p.title, slug=slug, summary=p.summary, body=p.body, pinned=p.pinned,
                status=PostStatus(p.status), published_at=utcnow() if p.status == "PUBLISHED" else None)
    db.add(post)
    db.flush()
    audit(db, request, actor_admin_id=ctx.id, action="post.create", entity_type="Post", entity_id=post.id,
          after={"title": post.title, "status": post.status.value})
    db.commit()
    return _post_out(post)


@router.patch("/posts/{post_id}")
def update_post(post_id: uuid.UUID, p: PostPatch, request: Request, ctx: AdminContext = Depends(need("post.write")),
                db: Session = Depends(get_db)):
    post = _get_or_404(db, Post, post_id, "Post")
    if post.deleted_at:
        raise HTTPException(404, "Post not found")
    live_change = post.status != PostStatus.DRAFT or (p.status and p.status != "DRAFT")
    if live_change and not ctx.has("post.publish"):
        raise HTTPException(403, "Only someone with the publish permission can change live posts.")
    before = _post_out(post)
    for f in ("title", "summary", "body", "pinned"):
        if getattr(p, f) is not None:
            setattr(post, f, getattr(p, f))
    if p.status:
        new = PostStatus(p.status)
        if new == PostStatus.PUBLISHED and post.published_at is None:
            post.published_at = utcnow()
        post.status = new
    audit(db, request, actor_admin_id=ctx.id, action="post.update", entity_type="Post", entity_id=post.id,
          before={k: before[k] for k in ("title", "status", "pinned")},
          after={"title": post.title, "status": post.status.value, "pinned": post.pinned})
    db.commit()
    return _post_out(post)


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("post.delete")), db: Session = Depends(get_db)):
    post = _get_or_404(db, Post, post_id, "Post")
    post.deleted_at, post.status = utcnow(), PostStatus.ARCHIVED
    audit(db, request, actor_admin_id=ctx.id, action="post.delete", entity_type="Post", entity_id=post.id, before={"title": post.title})
    db.commit()


# =============================================================== deposits & withdrawals
@router.get("/deposits")
def list_deposits(status: str = "PENDING", ctx: AdminContext = Depends(need("deposit.review")), db: Session = Depends(get_db)):
    q = db.query(DepositRequest, User).join(User, User.id == DepositRequest.user_id)
    if status.upper() in DepositStatus.__members__:
        q = q.filter(DepositRequest.status == DepositStatus[status.upper()])
    return [{"id": str(d.id), "user": u.full_name, "method": d.method.value, "amount": str(d.amount),
             "transaction_id": d.transaction_id, "status": d.status.value, "created_at": d.created_at.isoformat() + "Z"}
            for d, u in q.order_by(DepositRequest.created_at.desc()).limit(200)]


@router.post("/deposits/{deposit_id}/approve")
def approve_deposit(deposit_id: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("deposit.review")),
                    db: Session = Depends(get_db)):
    d = db.query(DepositRequest).filter(DepositRequest.id == deposit_id).with_for_update().first()
    if d is None:
        raise HTTPException(404, "Deposit not found")
    if d.status != DepositStatus.PENDING:
        raise HTTPException(409, f"Deposit is already {d.status.value.lower()}")
    try:
        credit_wallet(db, user_id=d.user_id, amount=d.amount, txn_type=WalletTxnType.DEPOSIT,
                      reference_type="deposit_request", reference_id=str(d.id), idempotency_key=f"deposit:{d.id}")
    except DuplicateTransactionError:
        raise HTTPException(409, "This deposit was already credited.")
    d.status, d.reviewed_by, d.reviewed_at = DepositStatus.APPROVED, ctx.id, utcnow()
    audit(db, request, actor_admin_id=ctx.id, action="deposit.approve", entity_type="DepositRequest", entity_id=d.id,
          before={"status": "PENDING"}, after={"status": "APPROVED", "amount": str(d.amount)})
    db.commit()
    return {"status": "APPROVED"}


@router.post("/deposits/{deposit_id}/reject")
def reject_deposit(deposit_id: uuid.UUID, p: Reason, request: Request, ctx: AdminContext = Depends(need("deposit.review")),
                   db: Session = Depends(get_db)):
    d = db.query(DepositRequest).filter(DepositRequest.id == deposit_id).with_for_update().first()
    if d is None:
        raise HTTPException(404, "Deposit not found")
    if d.status != DepositStatus.PENDING:
        raise HTTPException(409, f"Deposit is already {d.status.value.lower()}")
    d.status, d.reviewed_by, d.reviewed_at, d.note = DepositStatus.REJECTED, ctx.id, utcnow(), p.reason
    audit(db, request, actor_admin_id=ctx.id, action="deposit.reject", entity_type="DepositRequest", entity_id=d.id,
          before={"status": "PENDING"}, after={"status": "REJECTED", "reason": p.reason})
    db.commit()
    return {"status": "REJECTED"}


@router.get("/withdrawals")
def list_withdrawals(status: str = "PENDING", ctx: AdminContext = Depends(need("withdrawal.review")), db: Session = Depends(get_db)):
    q = db.query(Withdrawal, User).join(User, User.id == Withdrawal.user_id)
    if status.upper() in WithdrawalStatus.__members__:
        q = q.filter(Withdrawal.status == WithdrawalStatus[status.upper()])
    return [{"id": str(w.id), "user": u.full_name, "amount": str(w.amount), "destination": w.destination_account,
             "status": w.status.value, "requested_at": w.requested_at.isoformat() + "Z"}
            for w, u in q.order_by(Withdrawal.requested_at.desc()).limit(200)]


def _locked_pending_withdrawal(db: Session, wid: uuid.UUID) -> tuple[Withdrawal, Wallet]:
    w = db.query(Withdrawal).filter(Withdrawal.id == wid).with_for_update().first()
    if w is None:
        raise HTTPException(404, "Withdrawal not found")
    if w.status != WithdrawalStatus.PENDING:
        raise HTTPException(409, f"Withdrawal is already {w.status.value.lower()}")
    wallet = db.query(Wallet).filter(Wallet.user_id == w.user_id).with_for_update().one()
    return w, wallet


@router.post("/withdrawals/{wid}/approve")
def approve_withdrawal(wid: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("withdrawal.review")),
                       db: Session = Depends(get_db)):
    """Marks as paid. Send the money first (Telebirr / bank), then approve here."""
    w, wallet = _locked_pending_withdrawal(db, wid)
    wallet.pending_balance = wallet.pending_balance - w.amount
    w.status, w.processed_at, w.processed_by = WithdrawalStatus.SUCCESS, utcnow(), ctx.id
    audit(db, request, actor_admin_id=ctx.id, action="withdrawal.approve", entity_type="Withdrawal", entity_id=w.id,
          before={"status": "PENDING"}, after={"status": "SUCCESS", "amount": str(w.amount)})
    db.commit()
    return {"status": "SUCCESS"}


@router.post("/withdrawals/{wid}/reject")
def reject_withdrawal(wid: uuid.UUID, p: Reason, request: Request, ctx: AdminContext = Depends(need("withdrawal.review")),
                      db: Session = Depends(get_db)):
    w, wallet = _locked_pending_withdrawal(db, wid)
    wallet.pending_balance = wallet.pending_balance - w.amount
    try:
        credit_wallet(db, user_id=w.user_id, amount=w.amount, txn_type=WalletTxnType.REFUND,
                      reference_type="withdrawal", reference_id=str(w.id), idempotency_key=f"withdrawal-refund:{w.id}")
    except DuplicateTransactionError:
        raise HTTPException(409, "Already refunded")
    w.status, w.processed_at, w.processed_by, w.failure_reason = WithdrawalStatus.FAILED, utcnow(), ctx.id, p.reason
    audit(db, request, actor_admin_id=ctx.id, action="withdrawal.reject", entity_type="Withdrawal", entity_id=w.id,
          before={"status": "PENDING"}, after={"status": "FAILED", "reason": p.reason})
    db.commit()
    return {"status": "FAILED"}


# =============================================================== referrals
@router.get("/referrals")
def list_referrals(status: str = "HELD", ctx: AdminContext = Depends(need("referral.manage")), db: Session = Depends(get_db)):
    q = db.query(Referral)
    if status.upper() in ReferralStatus.__members__:
        q = q.filter(Referral.status == ReferralStatus[status.upper()])
    out = []
    for r in q.order_by(Referral.created_at.desc()).limit(200):
        a, b = db.get(User, r.referrer_id), db.get(User, r.referee_id)
        out.append({"id": str(r.id), "referrer": a.full_name if a else None, "referee": b.full_name if b else None,
                    "status": r.status.value, "note": r.note, "created_at": r.created_at.isoformat() + "Z"})
    return out


@router.post("/referrals/{rid}/approve")
def approve_referral(rid: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("referral.manage")), db: Session = Depends(get_db)):
    r = db.query(Referral).filter(Referral.id == rid).with_for_update().first()
    if r is None:
        raise HTTPException(404, "Referral not found")
    if r.status != ReferralStatus.HELD:
        raise HTTPException(409, f"Referral is {r.status.value.lower()}")
    referral_svc.pay(db, r, by_admin=True)
    audit(db, request, actor_admin_id=ctx.id, action="referral.approve", entity_type="Referral", entity_id=r.id,
          before={"status": "HELD"}, after={"status": "REWARDED", "amount": str(r.reward_amount)})
    db.commit()
    return {"status": "REWARDED"}


@router.post("/referrals/{rid}/void")
def void_referral(rid: uuid.UUID, p: Reason, request: Request, ctx: AdminContext = Depends(need("referral.manage")), db: Session = Depends(get_db)):
    r = db.query(Referral).filter(Referral.id == rid).with_for_update().first()
    if r is None:
        raise HTTPException(404, "Referral not found")
    if r.status not in (ReferralStatus.HELD, ReferralStatus.PENDING):
        raise HTTPException(409, f"Referral is {r.status.value.lower()}")
    before = r.status.value
    r.status, r.note = ReferralStatus.VOIDED, p.reason
    audit(db, request, actor_admin_id=ctx.id, action="referral.void", entity_type="Referral", entity_id=r.id,
          before={"status": before}, after={"status": "VOIDED", "reason": p.reason})
    db.commit()
    return {"status": "VOIDED"}


# =============================================================== gameweeks, fraud, settings
class GameweekIn(In):
    gw_number: int = Field(ge=1, le=60)
    entry_fee: Decimal = Field(ge=0, le=100000, max_digits=14, decimal_places=2)
    registration_deadline: datetime


@router.get("/gameweeks")
def list_gameweeks(ctx: AdminContext = Depends(need("gameweek.manage")), db: Session = Depends(get_db)):
    out = []
    for gw in db.query(Gameweek).order_by(Gameweek.gw_number.desc()).limit(60):
        n = db.query(func.count(CompetitionEntry.id)).filter(CompetitionEntry.gameweek_id == gw.id).scalar() or 0
        out.append({"id": str(gw.id), "gw_number": gw.gw_number, "entry_fee": str(gw.entry_fee), "status": gw.status.value,
                    "deadline": gw.registration_deadline.isoformat() + "Z", "participants": n})
    return out


@router.post("/gameweeks", status_code=201)
def create_gameweek(p: GameweekIn, request: Request, ctx: AdminContext = Depends(need("gameweek.manage")), db: Session = Depends(get_db)):
    if db.query(Gameweek.id).filter(Gameweek.gw_number == p.gw_number).first():
        raise HTTPException(409, f"Gameweek {p.gw_number} already exists")
    deadline = p.registration_deadline.replace(tzinfo=None) if p.registration_deadline.tzinfo is None else \
        p.registration_deadline.astimezone(tz=None).replace(tzinfo=None)
    gw = Gameweek(gw_number=p.gw_number, entry_fee=p.entry_fee, registration_deadline=deadline)
    db.add(gw)
    db.flush()
    db.add(GameweekConfig(gameweek_id=gw.id))
    audit(db, request, actor_admin_id=ctx.id, action="gameweek.create", entity_type="Gameweek", entity_id=gw.id,
          after={"gw": gw.gw_number, "fee": str(gw.entry_fee)})
    db.commit()
    return {"id": str(gw.id)}


@router.get("/fraud-alerts")
def fraud_alerts(ctx: AdminContext = Depends(need("fraud.manage")), db: Session = Depends(get_db)):
    return [{"id": str(a.id), "category": a.category, "severity": a.severity.value, "description": a.description,
             "user_id": str(a.related_user_id) if a.related_user_id else None, "resolved": a.resolved,
             "created_at": a.created_at.isoformat() + "Z"}
            for a in db.query(FraudAlert).order_by(FraudAlert.resolved, FraudAlert.created_at.desc()).limit(200)]


@router.post("/fraud-alerts/{alert_id}/resolve")
def resolve_alert(alert_id: uuid.UUID, p: Reason, request: Request, ctx: AdminContext = Depends(need("fraud.manage")),
                  db: Session = Depends(get_db)):
    a = _get_or_404(db, FraudAlert, alert_id, "Alert")
    a.resolved, a.resolved_at, a.resolved_by = True, utcnow(), ctx.id
    audit(db, request, actor_admin_id=ctx.id, action="fraud.resolve", entity_type="FraudAlert", entity_id=a.id, after={"reason": p.reason})
    db.commit()
    return {"resolved": True}


class SettingsIn(In):
    ads_enabled: bool


@router.get("/settings")
def get_settings(ctx: AdminContext = Depends(get_admin), db: Session = Depends(get_db)):
    return {"ads_enabled": get_platform_settings(db).ads_enabled}


@router.patch("/settings")
def update_settings(p: SettingsIn, request: Request, ctx: AdminContext = Depends(need("settings.manage")), db: Session = Depends(get_db)):
    row = get_platform_settings(db)
    before = {"ads_enabled": row.ads_enabled}
    row.ads_enabled = p.ads_enabled
    audit(db, request, actor_admin_id=ctx.id, action="settings.update", entity_type="PlatformSettings", entity_id=1,
          before=before, after={"ads_enabled": p.ads_enabled})
    db.commit()
    return {"ads_enabled": row.ads_enabled}


# =============================================================== audit
@router.get("/audit-log")
def audit_log(action: str = "", ctx: AdminContext = Depends(need("audit.read")), db: Session = Depends(get_db)):
    q = db.query(AuditLog, AdminAccount.email).outerjoin(AdminAccount, AdminAccount.id == AuditLog.actor_admin_id)
    if action:
        q = q.filter(AuditLog.action.like(action[:40] + "%"))
    return [{"id": str(l.id), "actor": email, "action": l.action, "entity": f"{l.entity_type}:{l.entity_id}",
             "before": l.before_state, "after": l.after_state, "ip": l.ip_address, "at": l.created_at.isoformat() + "Z"}
            for l, email in q.order_by(AuditLog.created_at.desc()).limit(200)]


@router.get("/security-events")
def security_events(ctx: AdminContext = Depends(need("audit.read")), db: Session = Depends(get_db)):
    return [{"id": str(e.id), "type": e.event_type, "severity": e.severity.value, "user_id": str(e.user_id) if e.user_id else None,
             "at": e.created_at.isoformat() + "Z"}
            for e in db.query(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(200)]


# =============================================================== admin accounts
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


class AdminCreate(In):
    email: str = Field(max_length=254)
    full_name: str = Field(min_length=2, max_length=120)
    role: str = Field(max_length=64)
    password: str = Field(max_length=200)


class AdminPatch(In):
    role: str | None = Field(default=None, max_length=64)
    status: Literal["ACTIVE", "DISABLED"] | None = None


def _active_super_admins(db: Session) -> int:
    return db.query(func.count(AdminAccount.id)).join(Role, Role.id == AdminAccount.role_id).filter(
        Role.name == "SUPER_ADMIN", AdminAccount.status == AdminStatus.ACTIVE).scalar() or 0


def _revoke_admin_sessions(db: Session, admin_id: uuid.UUID) -> None:
    for s in db.query(AdminSession).filter(AdminSession.admin_id == admin_id, AdminSession.revoked_at.is_(None)):
        s.revoked_at = utcnow()


@router.get("/roles")
def roles(ctx: AdminContext = Depends(need("admin.manage")), db: Session = Depends(get_db)):
    return [{"name": r.name, "description": r.description, "permissions": sorted(p.key for p in r.permissions)}
            for r in db.query(Role).order_by(Role.name)]


@router.get("/admins")
def list_admins(ctx: AdminContext = Depends(need("admin.manage")), db: Session = Depends(get_db)):
    return [{"id": str(a.id), "email": a.email, "full_name": a.full_name, "role": a.role.name, "status": a.status.value,
             "last_login_at": a.last_login_at.isoformat() + "Z" if a.last_login_at else None}
            for a in db.query(AdminAccount).order_by(AdminAccount.created_at)]


@router.post("/admins", status_code=201)
def create_admin(p: AdminCreate, request: Request, ctx: AdminContext = Depends(need("admin.manage")), db: Session = Depends(get_db)):
    email = p.email.lower()
    if not _EMAIL.fullmatch(email):
        raise HTTPException(422, "Enter a valid email address.")
    if not password_is_strong(p.password):
        raise HTTPException(422, "Use at least 12 characters with letters and numbers.")
    role = db.query(Role).filter(Role.name == p.role).first()
    if role is None:
        raise HTTPException(422, "Unknown role.")
    if db.query(AdminAccount.id).filter(AdminAccount.email == email).first():
        raise HTTPException(409, "An admin with this email already exists.")
    plain, enc = new_totp_secret()
    a = AdminAccount(email=email, full_name=p.full_name, password_hash=hash_password(p.password), role_id=role.id,
                     mfa_secret_encrypted=enc, created_by=ctx.id, password_changed_at=utcnow())
    db.add(a)
    db.flush()
    audit(db, request, actor_admin_id=ctx.id, action="admin.create", entity_type="AdminAccount", entity_id=a.id,
          after={"email": email, "role": role.name})
    db.commit()
    # Shown exactly once. The new admin scans it into an authenticator app.
    return {"id": str(a.id), "totp_uri": totp_uri(plain, email)}


@router.patch("/admins/{admin_id}")
def update_admin(admin_id: uuid.UUID, p: AdminPatch, request: Request, ctx: AdminContext = Depends(need("admin.manage")),
                 db: Session = Depends(get_db)):
    a = _get_or_404(db, AdminAccount, admin_id, "Admin")
    if a.id == ctx.id:
        raise HTTPException(403, "You can't change your own role or status.")
    before = {"role": a.role.name, "status": a.status.value}
    was_super = a.role.name == "SUPER_ADMIN" and a.status == AdminStatus.ACTIVE
    if p.role:
        role = db.query(Role).filter(Role.name == p.role).first()
        if role is None:
            raise HTTPException(422, "Unknown role.")
        a.role_id, a.role = role.id, role
    if p.status:
        a.status = AdminStatus(p.status)
    if was_super and not (a.role.name == "SUPER_ADMIN" and a.status == AdminStatus.ACTIVE) and _active_super_admins(db) <= 1:
        db.rollback()
        raise HTTPException(409, "There must always be at least one active super admin.")
    if p.status == "DISABLED" or p.role:
        _revoke_admin_sessions(db, a.id)          # permissions changed: force a fresh sign-in
    audit(db, request, actor_admin_id=ctx.id, action="admin.update", entity_type="AdminAccount", entity_id=a.id,
          before=before, after={"role": a.role.name, "status": a.status.value})
    db.commit()
    return {"ok": True}


@router.post("/admins/{admin_id}/reset-mfa")
def reset_mfa(admin_id: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("admin.manage")), db: Session = Depends(get_db)):
    a = _get_or_404(db, AdminAccount, admin_id, "Admin")
    plain, a.mfa_secret_encrypted = new_totp_secret()
    _revoke_admin_sessions(db, a.id)
    audit(db, request, actor_admin_id=ctx.id, action="admin.reset_mfa", entity_type="AdminAccount", entity_id=a.id)
    db.commit()
    return {"totp_uri": totp_uri(plain, a.email)}


@router.post("/admins/{admin_id}/revoke-sessions", status_code=204)
def revoke_sessions(admin_id: uuid.UUID, request: Request, ctx: AdminContext = Depends(need("admin.manage")), db: Session = Depends(get_db)):
    _get_or_404(db, AdminAccount, admin_id, "Admin")
    _revoke_admin_sessions(db, admin_id)
    audit(db, request, actor_admin_id=ctx.id, action="admin.revoke_sessions", entity_type="AdminAccount", entity_id=admin_id)
    db.commit()
