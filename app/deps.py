import uuid
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AdminAccount, AdminSession, AdminStatus, User, UserSession, utcnow
from app.security import decode_user_token, safe_equal, sha256_hex

ADMIN_COOKIE = "admin_session"
_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


# ------------------------------------------------------------------ end users
def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    """
    The ONLY source of "who is asking". Every user-scoped query must filter on the
    returned user's id; no endpoint may accept a user id from the request.
    """
    if creds is None:
        raise _unauthorized()
    claims = decode_user_token(creds.credentials)
    if claims is None:
        raise _unauthorized()
    try:
        user_id, sid = uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"])
    except ValueError:
        raise _unauthorized()
    sess = db.get(UserSession, sid)
    if sess is None or sess.user_id != user_id or sess.revoked_at is not None or sess.expires_at < utcnow():
        raise _unauthorized()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


def require_verified_user(user: User = Depends(get_current_user)) -> User:
    """Phone + Telegram verified AND an FPL team linked. Gate for anything involving money or scores."""
    if not user.is_phone_verified or user.fpl_team is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Finish verification before using this feature.")
    return user


# ------------------------------------------------------------------ admins
class AdminContext:
    def __init__(self, admin: AdminAccount, session: AdminSession):
        self.admin, self.session = admin, session
        self.id = admin.id
        self.permissions = admin.permission_keys

    def has(self, perm: str) -> bool:
        return perm in self.permissions


def get_admin(request: Request, db: Session = Depends(get_db)) -> AdminContext:
    raw = request.cookies.get(ADMIN_COOKIE)
    if not raw:
        raise _unauthorized()
    sess = db.query(AdminSession).filter(AdminSession.token_hash == sha256_hex(raw)).first()
    now = utcnow()
    if (
        sess is None or sess.revoked_at is not None or sess.expires_at < now
        or now - sess.last_seen_at > timedelta(minutes=settings.admin_session_idle_minutes)
    ):
        raise _unauthorized()
    admin = db.get(AdminAccount, sess.admin_id)
    if admin is None or admin.status != AdminStatus.ACTIVE:
        raise _unauthorized()

    # CSRF: every state-changing request must echo the per-session token.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not safe_equal(sent, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token")

    sess.last_seen_at = now
    db.flush()
    return AdminContext(admin, sess)


def require_permission(*perms: str):
    """Admin must hold ALL listed permissions."""
    def dependency(ctx: AdminContext = Depends(get_admin)) -> AdminContext:
        missing = [p for p in perms if not ctx.has(p)]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to do this.")
        return ctx
    return dependency
