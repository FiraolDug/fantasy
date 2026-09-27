"""
Runtime, admin-editable platform settings (as opposed to deployment-time
env vars in app/config.py). Backed by a single-row table so every reader
gets a consistent, always-present record instead of juggling None checks.
"""
from sqlalchemy.orm import Session

from app.models import PlatformSettings

_SETTINGS_ROW_ID = 1


def get_platform_settings(db: Session) -> PlatformSettings:
    settings_row = db.query(PlatformSettings).filter(PlatformSettings.id == _SETTINGS_ROW_ID).first()
    if settings_row is None:
        settings_row = PlatformSettings(id=_SETTINGS_ROW_ID)
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row
