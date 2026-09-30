"""
FPL team verification.

Flow (each step is a separate server call; the client never decides anything):
  1. manager_id  -> server fetches FPL, requires country == ET. Returns nothing about the team.
  2. team_name   -> server re-fetches FPL and requires an exact name match for that same ID.
  3. ownership   -> (when REQUIRE_OWNERSHIP_CHALLENGE) user temporarily renames the FPL team to a
                    one-time code; server sees the code through the FPL API and only then links.

Why step 3 exists: a Manager ID and team name are both public (they appear in every league table),
so "ID + name match" proves the person knows public facts, not that they own the account. The code
proves control of the FPL login. Without it, anyone could claim any Ethiopian manager's team.

Design rules that keep other people's data private:
  * responses never contain FPL data for an ID that isn't already the caller's verified team
  * "not found" and "not in Ethiopia" are one indistinguishable outcome
  * the team name is compared, never echoed, and nothing from FPL is stored until verification succeeds
  * the manager ID used in steps 2-3 comes from the server-side record, not the request body
"""
import secrets
import unicodedata
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    FplTeam, FplVerification, FplVerificationAttempt, FraudAlert, Severity, User, VerificationMethod,
    VerificationOutcome as O, VerificationStage as S, VerificationStatus as VS, utcnow,
)
from app.security import ip_hash
from app.services.audit import security_event
from app.services.fpl_client import (
    FPLManagerNotFound, FPLUpstreamError, fetch_manager_entry, names_match, normalize_team_name, parse_manager_id,
)
from app.services.rate_limit import client_ip
from app.services.referrals import reward_if_due

_COUNTED_FAILURES = (O.MANAGER_REJECTED, O.NAME_MISMATCH, O.ALREADY_LINKED)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I
MAX_CHALLENGE_CHECKS = 20


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message, **extra})


# ------------------------------------------------------------------ bookkeeping
def _record(db: Session, request: Request, user: User, ver: FplVerification | None,
            stage: S, outcome: O, manager_id: str) -> None:
    db.add(FplVerificationAttempt(
        user_id=user.id, verification_id=ver.id if ver else None, stage=stage, outcome=outcome,
        manager_id=manager_id, ip_hash=ip_hash(client_ip(request)),
    ))


def _fail(db: Session, request: Request, user: User, ver: FplVerification | None, stage: S, outcome: O,
          manager_id: str, status: int, code: str, message: str) -> HTTPException:
    _record(db, request, user, ver, stage, outcome, manager_id)
    if outcome in _COUNTED_FAILURES:
        security_event(db, request, f"fpl_verification_{outcome.value.lower()}", severity=Severity.LOW,
                       user_id=user.id, details={"stage": stage.value})
    db.commit()
    return _err(status, code, message, attempts_left=_attempts_left(db, user))


def _failed_last_day(db: Session, user_id) -> int:
    since = utcnow() - timedelta(hours=24)
    return db.query(func.count(FplVerificationAttempt.id)).filter(
        FplVerificationAttempt.user_id == user_id, FplVerificationAttempt.created_at >= since,
        FplVerificationAttempt.outcome.in_(_COUNTED_FAILURES)).scalar() or 0


def _attempts_left(db: Session, user: User) -> int:
    return max(0, settings.verification_max_failed_per_day - _failed_last_day(db, user.id))


def _flag(db: Session, user: User, category: str, description: str, severity: Severity, **details) -> None:
    exists = db.query(FraudAlert.id).filter(
        FraudAlert.related_user_id == user.id, FraudAlert.category == category, FraudAlert.resolved.is_(False)).first()
    if not exists:
        db.add(FraudAlert(category=category, description=description, severity=severity,
                          related_user_id=user.id, details=details or None))


def _guard(db: Session, request: Request, user: User, manager_id: str | None) -> None:
    if not user.is_phone_verified:
        raise _err(403, "phone_required", "Share your phone number through Telegram first.")
    if user.fpl_team is not None:
        raise _err(409, "already_verified", "Your FPL team is already verified.")
    if settings.require_ethiopian_phone and not (user.phone_number or "").startswith("+251"):
        raise _err(403, "phone_not_ethiopian", "An Ethiopian phone number (+251) is required.")

    now = utcnow()
    if _failed_last_day(db, user.id) >= settings.verification_max_failed_per_day:
        _flag(db, user, "verification_lockout", "User hit the daily failed-verification limit.", Severity.MEDIUM)
        security_event(db, request, "fpl_verification_locked", severity=Severity.MEDIUM, user_id=user.id)
        db.commit()
        raise _err(429, "locked", "Too many failed attempts. Try again in 24 hours or contact support.")
    if manager_id:
        ids = {r[0] for r in db.query(FplVerificationAttempt.manager_id).filter(
            FplVerificationAttempt.user_id == user.id,
            FplVerificationAttempt.created_at >= now - timedelta(hours=24)).distinct()}
        if manager_id not in ids and len(ids) >= settings.verification_max_manager_ids_per_day:
            _flag(db, user, "manager_id_enumeration", "User tried many different Manager IDs in 24h.", Severity.HIGH)
            db.commit()
            raise _err(429, "too_many_ids", "You've tried too many different Manager IDs today.")
    h = ip_hash(client_ip(request))
    if h and (db.query(func.count(FplVerificationAttempt.id)).filter(
            FplVerificationAttempt.ip_hash == h,
            FplVerificationAttempt.created_at >= now - timedelta(hours=1)).scalar() or 0
              ) >= settings.verification_max_attempts_per_ip_hour:
        raise _err(429, "ip_limited", "Too many attempts from this network. Try again later.")


def _current(db: Session, user: User, *statuses: VS) -> FplVerification | None:
    return (db.query(FplVerification)
            .filter(FplVerification.user_id == user.id, FplVerification.status.in_(statuses))
            .order_by(FplVerification.created_at.desc()).first())


def _fetch(db, request, user, ver, stage, manager_id):
    try:
        return fetch_manager_entry(manager_id)
    except FPLManagerNotFound:
        return None
    except FPLUpstreamError:
        _record(db, request, user, ver, stage, O.UPSTREAM_ERROR, manager_id)
        db.commit()
        raise _err(503, "fpl_unavailable", "The FPL website isn't responding. Try again in a minute.")


# ------------------------------------------------------------------ step 1
def submit_manager_id(db: Session, request: Request, user: User, raw_id: str) -> dict:
    manager_id = parse_manager_id(raw_id)
    if manager_id is None:
        raise _err(422, "invalid_manager_id", "A Manager ID is a number with up to 10 digits.")
    _guard(db, request, user, manager_id)

    entry = _fetch(db, request, user, None, S.MANAGER_ID, manager_id)
    if entry is None or entry.country_code != settings.fpl_required_country:
        raise _fail(db, request, user, None, S.MANAGER_ID, O.MANAGER_REJECTED, manager_id, 422, "manager_rejected",
                    "We couldn't verify this Manager ID for Ethiopia. Check the number and make sure your "
                    "FPL profile country is set to Ethiopia.")

    for old in db.query(FplVerification).filter(
            FplVerification.user_id == user.id, FplVerification.status.in_((VS.MANAGER_ACCEPTED, VS.AWAITING_OWNERSHIP))):
        old.status = VS.EXPIRED
    ver = FplVerification(user_id=user.id, manager_id=manager_id, status=VS.MANAGER_ACCEPTED)
    db.add(ver)
    db.flush()
    _record(db, request, user, ver, S.MANAGER_ID, O.OK, manager_id)
    db.commit()
    return {"step": "team_name", "country": settings.fpl_required_country}


# ------------------------------------------------------------------ step 2
def _clean_team_name(raw: str) -> str | None:
    name = normalize_team_name(raw)
    if not (1 <= len(name) <= 64) or any(unicodedata.category(c) == "Cc" for c in name):
        return None
    return name


def submit_team_name(db: Session, request: Request, user: User, raw_name: str) -> dict:
    ver = _current(db, user, VS.MANAGER_ACCEPTED)
    if ver is None:
        raise _err(409, "no_manager_id", "Enter your Manager ID first.")
    name = _clean_team_name(raw_name)
    if name is None:
        raise _err(422, "invalid_team_name", "Enter your FPL team name exactly as it appears in FPL.")
    _guard(db, request, user, ver.manager_id)

    entry = _fetch(db, request, user, ver, S.TEAM_NAME, ver.manager_id)
    # Same message for "manager vanished", "wrong country" and "name differs".
    if entry is None or entry.country_code != settings.fpl_required_country or not names_match(name, entry.team_name):
        raise _fail(db, request, user, ver, S.TEAM_NAME, O.NAME_MISMATCH, ver.manager_id, 422, "name_mismatch",
                    "The team name doesn't match this Manager ID, so we can't verify you. Enter the name exactly "
                    "as shown in FPL: same spelling, capital letters, spaces and emoji.")

    owner = db.query(FplTeam).filter(FplTeam.manager_id == ver.manager_id).first()
    if owner is not None:
        _flag(db, user, "manager_id_claim_conflict",
              "User passed ID+name for a Manager ID already linked to another account.", Severity.HIGH,
              manager_id=ver.manager_id, existing_owner=str(owner.user_id))
        raise _fail(db, request, user, ver, S.TEAM_NAME, O.ALREADY_LINKED, ver.manager_id, 409, "already_linked",
                    "This FPL team is already linked to another account. If it's yours, contact support.")

    ver.team_name_submitted = name
    if settings.require_ownership_challenge:
        ver.status = VS.AWAITING_OWNERSHIP
        ver.challenge_code = "FPL-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        ver.challenge_expires_at = utcnow() + timedelta(minutes=settings.challenge_ttl_minutes)
        _record(db, request, user, ver, S.TEAM_NAME, O.OK, ver.manager_id)
        db.commit()
        return {"step": "ownership", "challenge": _challenge(ver)}
    return _finalize(db, request, user, ver, entry.country_code, VerificationMethod.NAME_MATCH, name)


# ------------------------------------------------------------------ step 3
def check_ownership(db: Session, request: Request, user: User) -> dict:
    ver = _current(db, user, VS.AWAITING_OWNERSHIP)
    if ver is None:
        raise _err(409, "no_challenge", "There's no ownership check in progress.")
    if ver.challenge_expires_at < utcnow():
        ver.status = VS.EXPIRED
        _record(db, request, user, ver, S.OWNERSHIP, O.CHALLENGE_EXPIRED, ver.manager_id)
        db.commit()
        raise _err(410, "challenge_expired", "The code expired. Start again from your Manager ID.")
    _guard(db, request, user, ver.manager_id)
    checks = db.query(func.count(FplVerificationAttempt.id)).filter(
        FplVerificationAttempt.verification_id == ver.id, FplVerificationAttempt.stage == S.OWNERSHIP).scalar() or 0
    if checks >= MAX_CHALLENGE_CHECKS:
        raise _err(429, "too_many_checks", "Too many checks. Wait for the code to expire and start again.")

    entry = _fetch(db, request, user, ver, S.OWNERSHIP, ver.manager_id)
    if entry is None or entry.country_code != settings.fpl_required_country \
            or normalize_team_name(entry.team_name) != ver.challenge_code:
        # Not counted toward the daily lockout: FPL can take a minute to show a rename.
        raise _fail(db, request, user, ver, S.OWNERSHIP, O.CHALLENGE_FAILED, ver.manager_id, 422, "code_not_found",
                    "We can't see the code in your FPL team name yet. Save the new name on the FPL site, wait "
                    "a moment, then check again.")
    return _finalize(db, request, user, ver, entry.country_code, VerificationMethod.NAME_MATCH_PLUS_CODE,
                     ver.team_name_submitted)


# ------------------------------------------------------------------ finish
def _finalize(db: Session, request: Request, user: User, ver: FplVerification, country: str,
              method: VerificationMethod, team_name: str) -> dict:
    team = FplTeam(user_id=user.id, manager_id=ver.manager_id, team_name=team_name,
                   team_name_normalized=normalize_team_name(team_name).casefold(),
                   country_code=country, verification_method=method)
    db.add(team)
    ver.status, ver.completed_at, ver.challenge_code = VS.VERIFIED, utcnow(), None
    _record(db, request, user, ver, S.OWNERSHIP if method == VerificationMethod.NAME_MATCH_PLUS_CODE else S.TEAM_NAME,
            O.OK, ver.manager_id)
    security_event(db, request, "fpl_verified", user_id=user.id, details={"method": method.value})
    try:
        db.flush()   # the UNIQUE constraints on user_id / manager_id are the race-proof guard
    except IntegrityError:
        db.rollback()
        raise _err(409, "already_linked", "This FPL team is already linked to an account.")

    h = ip_hash(client_ip(request))
    if h:
        n = db.query(func.count(func.distinct(FplVerificationAttempt.user_id))).join(
            FplVerification, FplVerification.id == FplVerificationAttempt.verification_id).filter(
            FplVerificationAttempt.ip_hash == h, FplVerification.status == VS.VERIFIED,
            FplVerification.completed_at >= utcnow() - timedelta(hours=24)).scalar() or 0
        if n >= 3:
            _flag(db, user, "shared_network_accounts", f"{n} accounts verified from one network in 24h.",
                  Severity.MEDIUM, accounts=n)
    if user.phone_number and not user.phone_number.startswith("+251"):
        _flag(db, user, "non_ethiopian_phone", "Verified Ethiopian FPL team with a non-+251 phone.", Severity.LOW)
    reward_if_due(db, request, user, "verified")
    db.commit()
    return {"step": "done", "team": _team_out(team)}


# ------------------------------------------------------------------ read model (caller's own data only)
def _challenge(ver: FplVerification) -> dict:
    return {"code": ver.challenge_code, "expires_at": ver.challenge_expires_at.isoformat() + "Z"}


def _team_out(team: FplTeam) -> dict:
    return {"manager_id": team.manager_id, "team_name": team.team_name, "country": team.country_code}


def status_for(db: Session, user: User) -> dict:
    if user.fpl_team is not None:
        return {"phone_verified": True, "telegram_verified": True, "verified": True, "step": "done",
                "team": _team_out(user.fpl_team), "challenge": None, "attempts_left": None}
    step, challenge = "phone", None
    if user.is_phone_verified:
        step = "manager_id"
        ver = _current(db, user, VS.MANAGER_ACCEPTED, VS.AWAITING_OWNERSHIP)
        if ver is not None and ver.status == VS.MANAGER_ACCEPTED:
            step = "team_name"
        elif ver is not None and ver.challenge_expires_at and ver.challenge_expires_at > utcnow():
            step, challenge = "ownership", _challenge(ver)
    return {"phone_verified": user.is_phone_verified, "telegram_verified": user.telegram_verified_at is not None,
            "verified": False, "step": step, "team": None, "challenge": challenge,
            "attempts_left": _attempts_left(db, user)}
