"""
Creates tables, seeds roles/permissions, settings and the first super admin.
Prints the super admin's authenticator (TOTP) URI ONCE when the account is created.
Use Alembic for schema changes after the first release:  alembic revision --autogenerate
"""
from app import models  # noqa: F401
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import AdminAccount, PlatformSettings, Role, utcnow
from app.security import hash_password, new_totp_secret, password_is_strong, totp_uri
from app.services.rbac import seed_rbac


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_rbac(db)
        email = settings.admin_email.strip().lower()
        if db.query(AdminAccount).filter(AdminAccount.email == email).first() is None:
            if not password_is_strong(settings.admin_password):
                raise SystemExit("ADMIN_PASSWORD must be at least 12 characters with letters and numbers.")
            role = db.query(Role).filter(Role.name == "SUPER_ADMIN").one()
            plain, enc = new_totp_secret()
            db.add(AdminAccount(email=email, full_name="Super Admin", password_hash=hash_password(settings.admin_password),
                                role_id=role.id, mfa_secret_encrypted=enc, password_changed_at=utcnow()))
            db.commit()
            print(f"Created super admin {email}")
            print("Add this to an authenticator app now (it is not shown again):")
            print(totp_uri(plain, email))
        else:
            print("Super admin already exists.")
        if db.query(PlatformSettings).filter(PlatformSettings.id == 1).first() is None:
            db.add(PlatformSettings(id=1))
            db.commit()
    finally:
        db.close()
    print("Database ready.")


if __name__ == "__main__":
    main()
