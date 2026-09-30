"""Permission catalogue and default roles. Roles live in the database; admins are assigned exactly one."""
from sqlalchemy.orm import Session

from app.models import Permission, Role

PERMISSIONS: dict[str, str] = {
    "post.read": "See all posts, including drafts",
    "post.write": "Create posts and edit drafts",
    "post.publish": "Publish, archive and edit live posts",
    "post.delete": "Delete posts",
    "user.read": "See users (phone numbers masked)",
    "user.read_pii": "See full phone numbers (every view is audited)",
    "user.suspend": "Suspend or reinstate users",
    "verification.review": "Review verifications and unlink FPL teams",
    "deposit.review": "Approve or reject deposits",
    "withdrawal.review": "Approve or reject withdrawals",
    "gameweek.manage": "Create and manage gameweeks",
    "referral.manage": "See referrals; approve or void held rewards",
    "fraud.manage": "View and resolve fraud alerts",
    "audit.read": "Read audit log and security events",
    "admin.manage": "Manage admin accounts and roles",
    "settings.manage": "Change platform settings",
}

DEFAULT_ROLES: dict[str, tuple[str, set[str]]] = {
    "SUPER_ADMIN": ("Full access", set(PERMISSIONS)),
    "OPERATIONS_ADMIN": ("Content, users, verification and gameweeks", {
        "post.read", "post.write", "post.publish", "post.delete", "user.read", "user.suspend",
        "verification.review", "gameweek.manage", "fraud.manage", "referral.manage", "audit.read"}),
    "FINANCE_ADMIN": ("Deposits and withdrawals", {"user.read", "deposit.review", "withdrawal.review", "referral.manage", "audit.read"}),
    "CONTENT_EDITOR": ("Writes drafts; cannot publish", {"post.read", "post.write"}),
}


def seed_rbac(db: Session) -> None:
    """Idempotent. Creates missing permissions and roles; never overwrites a role someone customised."""
    perms = {p.key: p for p in db.query(Permission)}
    for key, desc in PERMISSIONS.items():
        if key not in perms:
            perms[key] = Permission(key=key, description=desc)
            db.add(perms[key])
    db.flush()
    existing = {r.name: r for r in db.query(Role)}
    for name, (desc, keys) in DEFAULT_ROLES.items():
        if name not in existing:
            db.add(Role(name=name, description=desc, is_system=True, permissions=[perms[k] for k in sorted(keys)]))
        elif name == "SUPER_ADMIN":      # super admin always holds every permission, including new ones
            have = {p.key for p in existing[name].permissions}
            existing[name].permissions.extend(perms[k] for k in sorted(keys - have))
    db.commit()
