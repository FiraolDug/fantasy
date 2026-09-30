import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

from cryptography.fernet import Fernet

os.environ.update(
    DATABASE_URL="sqlite://", SECRET_KEY="k" * 40, IP_HASH_SECRET="i" * 40, BOT_INTERNAL_SECRET="b" * 40,
    FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode(), ADMIN_EMAIL="root@example.test", ADMIN_PASSWORD="Str0ngPassw0rd!x",
    TELEBIRR_RECEIVER_NUMBER="0900000000", CBE_ACCOUNT_NUMBER="1000000000000", COOKIE_SECURE="false",
    BOT_TOKEN="123456:ABCdefGHIjklMNOpqrsTUVwxyz0123456789", PUBLIC_BASE_URL="https://example.test",
    TELEGRAM_WEBHOOK_SECRET="hook", REQUIRE_OWNERSHIP_CHALLENGE="false", TRUSTED_PROXY_COUNT="0",
)

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import models
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.security import hash_password, new_totp_secret
from app.services.rate_limit import reset_rate_limits
from app.services.rbac import seed_rbac

BOT_HEADERS = {"X-Bot-Secret": "b" * 40}


@event.listens_for(engine, "connect")
def _sqlite_fk(dbapi_conn, _):
    dbapi_conn.isolation_level = None          # let SQLAlchemy manage BEGIN/SAVEPOINT
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


@event.listens_for(engine, "begin")
def _sqlite_begin(conn):
    conn.exec_driver_sql("BEGIN")


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    reset_rate_limits()
    with SessionLocal() as db:
        seed_rbac(db)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def init_data(tg_id: int, first="Abel", age=0) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": tg_id, "first_name": first})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class Person:
    """A signed-in end user with a verified phone."""
    def __init__(self, client, tg_id, phone=None):
        self.client, self.tg_id = client, tg_id
        r = client.post("/auth/telegram-webapp", json={"init_data": init_data(tg_id)})
        assert r.status_code == 200, r.text
        self.h = {"Authorization": "Bearer " + r.json()["access_token"]}
        if phone:
            r = client.post("/internal/register", headers=BOT_HEADERS, json={
                "telegram_id": str(tg_id), "contact_user_id": str(tg_id), "phone_number": phone})
            assert r.status_code == 200, r.text

    def get(self, path): return self.client.get(path, headers=self.h)
    def post(self, path, json=None, headers=None): return self.client.post(path, json=json or {}, headers={**self.h, **(headers or {})})


@pytest.fixture
def make_person(client):
    return lambda tg_id, phone="+251911000001": Person(client, tg_id, phone)


@pytest.fixture
def fake_fpl(monkeypatch):
    """Replaces the outbound FPL call. fake_fpl.teams[manager_id] = (team_name, country)."""
    from app.services import verification
    from app.services.fpl_client import FPLEntry, FPLManagerNotFound

    class Fake:
        teams: dict = {}
    def fetch(manager_id):
        if manager_id not in Fake.teams:
            raise FPLManagerNotFound(manager_id)
        name, country = Fake.teams[manager_id]
        return FPLEntry(manager_id, name, country)
    Fake.teams = {}
    monkeypatch.setattr(verification, "fetch_manager_entry", fetch)
    return Fake


ADMIN_PASSWORD = "Str0ngPassw0rd!x"


def make_admin(email="root@example.test", role="SUPER_ADMIN", password=ADMIN_PASSWORD):
    plain, enc = new_totp_secret()
    with SessionLocal() as db:
        r = db.query(models.Role).filter_by(name=role).one()
        db.add(models.AdminAccount(email=email, full_name="Test Admin", password_hash=hash_password(password),
                                   role_id=r.id, mfa_secret_encrypted=enc))
        db.commit()
    return plain


def admin_login(client, email, secret, password=ADMIN_PASSWORD):
    r = client.post("/admin-api/auth/login", json={"email": email, "password": password, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}
