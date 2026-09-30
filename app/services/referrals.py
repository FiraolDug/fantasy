"""
Referral program: the referrer earns REFERRAL_REWARD ETB per referred person.

Anti-abuse rules (all server-side):
  * a code can only be applied once, by a new account that has no FPL team and no entries
  * you can't refer yourself; the referee is unique (one referrer per person, ever)
  * the reward is paid only when the referee does something that costs real effort/money
    (default: their first paid gameweek entry), never at sign-up
  * per-referrer cap on rewards
  * if referrer and referee ever shared a network (same hashed IP) the reward is HELD for an admin
  * payment goes through the ledger with an idempotency key, so it can never be paid twice
"""
import secrets
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    CompetitionEntry, FraudAlert, Referral, ReferralStatus, Severity, User, UserSession, WalletTxnType, utcnow,
)
from app.services.audit import security_event
from app.services.ledger import DuplicateTransactionError, credit_wallet

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def ensure_code(db: Session, user: User) -> str:
    if user.referral_code:
        return user.referral_code
    for _ in range(10):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if not db.query(User.id).filter(User.referral_code == code).first():
            user.referral_code = code
            db.commit()
            return code
    raise HTTPException(503, "Couldn't create a referral code. Try again.")


def apply_code(db: Session, request: Request, user: User, raw_code: str) -> None:
    code = (raw_code or "").strip().upper()
    if not (4 <= len(code) <= 12) or any(c not in _ALPHABET for c in code):
        raise HTTPException(422, "That referral code isn't valid.")
    if db.query(Referral.id).filter(Referral.referee_id == user.id).first():
        raise HTTPException(409, "You've already used a referral code.")
    if user.fpl_team is not None or db.query(CompetitionEntry.id).filter(CompetitionEntry.user_id == user.id).first():
        raise HTTPException(409, "Referral codes can only be used before you join.")
    if utcnow() - user.created_at > timedelta(days=settings.referral_max_account_age_days):
        raise HTTPException(409, "This account is too old to use a referral code.")
    referrer = db.query(User).filter(User.referral_code == code).first()
    if referrer is None or not referrer.is_active:
        raise HTTPException(422, "That referral code isn't valid.")     # same message: no code enumeration
    if referrer.id == user.id:
        raise HTTPException(422, "You can't use your own code.")
    db.add(Referral(referrer_id=referrer.id, referee_id=user.id))
    security_event(db, request, "referral_applied", user_id=user.id, details={"referrer": str(referrer.id)})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "You've already used a referral code.")


def _shared_network(db: Session, a, b) -> bool:
    hashes = lambda uid: {h for (h,) in db.query(UserSession.ip_hash).filter(UserSession.user_id == uid, UserSession.ip_hash.isnot(None))}  # noqa: E731
    return bool(hashes(a) & hashes(b))


def pay(db: Session, ref: Referral, *, by_admin: bool = False) -> None:
    """Credits the reward. Idempotent. Caller commits."""
    amount = Decimal(settings.referral_reward)
    try:
        credit_wallet(db, user_id=ref.referrer_id, amount=amount, txn_type=WalletTxnType.REFERRAL_BONUS,
                      reference_type="referral", reference_id=str(ref.id), idempotency_key=f"referral:{ref.id}")
    except DuplicateTransactionError:
        pass
    ref.status, ref.reward_amount, ref.rewarded_at = ReferralStatus.REWARDED, amount, utcnow()


def reward_if_due(db: Session, request: Request | None, referee: User, trigger: str) -> None:
    """Call inside the transaction that completes the trigger event (verification or first entry)."""
    if settings.referral_reward <= 0 or trigger != settings.referral_trigger:
        return
    ref = db.query(Referral).filter(Referral.referee_id == referee.id, Referral.status == ReferralStatus.PENDING) \
        .with_for_update().first()
    if ref is None:
        return
    referrer = db.get(User, ref.referrer_id)
    if referrer is None or not referrer.is_active:
        ref.status, ref.note = ReferralStatus.VOIDED, "Referrer not active"
        return
    paid = db.query(func.count(Referral.id)).filter(
        Referral.referrer_id == ref.referrer_id, Referral.status == ReferralStatus.REWARDED).scalar() or 0
    if paid >= settings.referral_max_rewards_per_user:
        ref.status, ref.note = ReferralStatus.HELD, "Referrer reached the reward cap"
    elif _shared_network(db, ref.referrer_id, ref.referee_id):
        ref.status, ref.note = ReferralStatus.HELD, "Referrer and referred user share a network"
        db.add(FraudAlert(category="referral_shared_network", severity=Severity.MEDIUM, related_user_id=ref.referrer_id,
                          description="Referral reward held: referrer and referred user signed in from the same network.",
                          details={"referral_id": str(ref.id)}))
    else:
        pay(db, ref)
        return
    security_event(db, request, "referral_held", severity=Severity.MEDIUM, user_id=ref.referrer_id,
                   details={"referral_id": str(ref.id), "why": ref.note})


def summary(db: Session, user: User) -> dict:
    code = ensure_code(db, user)
    rows = db.query(Referral.status, func.count(Referral.id)).filter(Referral.referrer_id == user.id) \
        .group_by(Referral.status).all()
    counts = {s.value: n for s, n in rows}
    rewarded = counts.get("REWARDED", 0)
    link = f"{settings.referral_link_base.rstrip('/')}?startapp={code}" if settings.referral_link_base else None
    already = db.query(Referral.id).filter(Referral.referee_id == user.id).first() is not None
    can_apply = (not already and user.fpl_team is None
                 and utcnow() - user.created_at <= timedelta(days=settings.referral_max_account_age_days))
    return {"code": code, "link": link, "reward": settings.referral_reward, "trigger": settings.referral_trigger,
            "invited": sum(counts.values()), "rewarded": rewarded, "pending": counts.get("PENDING", 0) + counts.get("HELD", 0),
            "earned": str(Decimal(settings.referral_reward) * rewarded), "can_apply_code": can_apply}
