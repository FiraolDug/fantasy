import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, SecurityEvent, Severity
from app.security import ip_hash
from app.services.rate_limit import client_ip


def _snapshot(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: (str(v) if not isinstance(v, (str, int, float, bool, type(None), dict, list)) else v) for k, v in d.items()}


def audit(db: Session, request: Request | None, *, actor_admin_id: uuid.UUID | None, action: str,
          entity_type: str, entity_id, before: dict | None = None, after: dict | None = None) -> None:
    """Administrative action record. Call inside the same transaction as the change itself."""
    db.add(AuditLog(
        actor_admin_id=actor_admin_id, action=action, entity_type=entity_type, entity_id=str(entity_id),
        before_state=_snapshot(before), after_state=_snapshot(after),
        ip_address=client_ip(request) if request else None,
        user_agent=(request.headers.get("user-agent", "")[:255] if request else None),
    ))


def security_event(db: Session, request: Request | None, event_type: str, *, severity: Severity = Severity.LOW,
                   user_id: uuid.UUID | None = None, admin_id: uuid.UUID | None = None,
                   details: dict | None = None) -> None:
    db.add(SecurityEvent(
        event_type=event_type, severity=severity, user_id=user_id, admin_id=admin_id,
        ip_hash=ip_hash(client_ip(request)) if request else None, details=_snapshot(details),
    ))
