"""
One-time bootstrap: creates all tables and seeds a super-admin user.
For an MVP this replaces Alembic; once the schema stabilizes, switch
to `alembic revision --autogenerate` + `alembic upgrade head` instead
of calling create_all in a live environment.

Usage:
    python init_db.py
"""
from app.config import settings
from app.database import Base, SessionLocal, engine
from app import models  # noqa: F401  (ensures all models are registered)
from app.models import User, UserRole
from app.security import hash_password


def main():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.phone_number == settings.admin_email).first()
        if existing is None:
            admin = User(
                phone_number=settings.admin_email,  # login identifier for admin/staff
                full_name="Super Admin",
                role=UserRole.SUPER_ADMIN,
                password_hash=hash_password(settings.admin_password),
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print(f"Created super admin: {settings.admin_email}")
        else:
            print("Super admin already exists, skipping.")
    finally:
        db.close()

    print("Database ready.")


if __name__ == "__main__":
    main()
