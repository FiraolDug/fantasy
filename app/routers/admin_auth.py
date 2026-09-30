from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import ADMIN_COOKIE, AdminContext, get_admin
from app.models import AdminAccount, AdminSession, AdminStatus, Severity, utcnow
from app.security import (
    hash_password, new_session_secret, password_is_strong, sha256_hex, verify_password, verify_totp,
)
from app.services.audit import audit, security_event
from app.services.rate_limit import client_ip, rate_limit

router = APIRouter(prefix="/admin-api/auth", tags=["admin-auth"])
MAX_FAILURES, LOCK_MINUTES = 5, 15


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    code: str = Field(default="", max_length=6)


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _me(ctx_admin: AdminAccount, csrf: str) -> dict:
    return {"id": str(ctx_admin.id), "email": ctx_admin.email, "full_name": ctx_admin.full_name,
            "role": ctx_admin.role.name, "permissions": sorted(ctx_admin.permission_keys), "csrf_token": csrf}


@router.post("/login", dependencies=[Depends(rate_limit("admin-login", 10, 300))])
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    now = utcnow()
    admin = db.query(AdminAccount).filter(AdminAccount.email == payload.email.strip().lower()).first()
    # Always run the password check so response time doesn't reveal whether the email exists.
    password_ok = verify_password(payload.password, admin.password_hash if admin else None)
    locked = bool(admin and admin.locked_until and admin.locked_until > now)
    active = bool(admin and admin.status == AdminStatus.ACTIVE)
    mfa_ok = True
    if admin and (settings.admin_mfa_required or admin.mfa_enabled):
        mfa_ok = verify_totp(admin.mfa_secret_encrypted, payload.code)

    if not (admin and password_ok and mfa_ok and active) or locked:
        if admin and not locked:
            admin.failed_login_attempts += 1
            if admin.failed_login_attempts >= MAX_FAILURES:
                admin.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                admin.failed_login_attempts = 0
        security_event(db, request, "admin_login_failed", severity=Severity.MEDIUM,
                       admin_id=admin.id if admin else None, details={"locked": locked})
        db.commit()
        raise HTTPException(401, "Invalid email, password or code.")

    admin.failed_login_attempts, admin.locked_until, admin.last_login_at = 0, None, now
    raw, csrf = new_session_secret(), new_session_secret()
    db.add(AdminSession(admin_id=admin.id, token_hash=sha256_hex(raw), csrf_token=csrf,
                        ip_address=client_ip(request), user_agent=request.headers.get("user-agent", "")[:255],
                        expires_at=now + timedelta(hours=settings.admin_session_absolute_hours)))
    security_event(db, request, "admin_login", admin_id=admin.id)
    db.commit()
    response.set_cookie(ADMIN_COOKIE, raw, httponly=True, secure=settings.cookie_secure, samesite="strict",
                        path="/admin-api", max_age=settings.admin_session_absolute_hours * 3600)
    return _me(admin, csrf)


@router.get("/me")
def me(ctx: AdminContext = Depends(get_admin)):
    return _me(ctx.admin, ctx.session.csrf_token)


@router.post("/logout", status_code=204)
def logout(response: Response, ctx: AdminContext = Depends(get_admin), db: Session = Depends(get_db)):
    ctx.session.revoked_at = utcnow()
    db.commit()
    response.delete_cookie(ADMIN_COOKIE, path="/admin-api")


@router.post("/change-password", status_code=204)
def change_password(payload: PasswordIn, request: Request, ctx: AdminContext = Depends(get_admin),
                    db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, ctx.admin.password_hash):
        raise HTTPException(403, "Current password is incorrect.")
    if not password_is_strong(payload.new_password):
        raise HTTPException(422, "Use at least 12 characters with letters and numbers.")
    ctx.admin.password_hash, ctx.admin.password_changed_at = hash_password(payload.new_password), utcnow()
    for s in db.query(AdminSession).filter(AdminSession.admin_id == ctx.id, AdminSession.revoked_at.is_(None),
                                           AdminSession.id != ctx.session.id):
        s.revoked_at = utcnow()          # sign out every other device
    audit(db, request, actor_admin_id=ctx.id, action="admin.change_password", entity_type="AdminAccount", entity_id=ctx.id)
    db.commit()
