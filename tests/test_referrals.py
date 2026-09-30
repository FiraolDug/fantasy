from datetime import timedelta
from decimal import Decimal

from app.config import settings
from app.database import SessionLocal
from app.models import Gameweek, Referral, ReferralStatus, User, Wallet, WalletTxnType, WalletTransaction, utcnow
from tests.conftest import admin_login, make_admin
from tests.test_verification import link


def _open_gameweek(fee=200):
    with SessionLocal() as db:
        gw = Gameweek(gw_number=1, entry_fee=fee, registration_deadline=utcnow() + timedelta(days=1))
        db.add(gw); db.commit()
        return str(gw.id)


def _fund(client, person, amount, tx):
    person.post("/deposits", {"method": "CBE", "amount": str(amount), "transaction_id": tx})
    with SessionLocal() as db:
        from app.models import DepositRequest
        dep = db.query(DepositRequest).filter_by(transaction_id=tx).one()
    return str(dep.id)


def _approve(client, dep_id):
    secret = make_admin("fin@example.test", "FINANCE_ADMIN")
    csrf = admin_login(client, "fin@example.test", secret)
    assert client.post(f"/admin-api/deposits/{dep_id}/approve", headers=csrf).status_code == 200


def _balance(uid_phone_tg):
    with SessionLocal() as db:
        u = db.query(User).filter_by(telegram_id=str(uid_phone_tg)).one()
        w = db.query(Wallet).filter_by(user_id=u.id).first()
        return Decimal(w.available_balance) if w else Decimal(0)


def _setup(client, make_person, fake_fpl):
    fake_fpl.teams.update({"1": ("Alpha", "ET"), "2": ("Bravo", "ET")})
    a, b = make_person(101, "+251911000101"), make_person(102, "+251911000102")
    with SessionLocal() as db:           # TestClient uses one address for everyone; give each user their own network
        from app.models import UserSession
        for i, sess in enumerate(db.query(UserSession).all()):
            sess.ip_hash = f"net-{i}"
        db.commit()
    code = a.get("/referrals/me").json()["code"]
    return a, b, code


def test_reward_only_after_first_paid_entry_and_only_once(client, make_person, fake_fpl):
    a, b, code = _setup(client, make_person, fake_fpl)
    assert b.post("/referrals/apply", {"code": code}).status_code == 204
    assert link(a, "1", "Alpha").status_code == 200 and link(b, "2", "Bravo").status_code == 200
    assert _balance(101) == 0                                   # verifying alone pays nothing (default trigger)
    gw = _open_gameweek()
    dep = _fund(client, b, 500, "TX0001"); _approve(client, dep)
    assert b.post(f"/gameweeks/{gw}/join").status_code == 200
    assert _balance(101) == Decimal(settings.referral_reward)
    me = a.get("/referrals/me").json()
    assert me["rewarded"] == 1 and me["earned"] == "10" and me["invited"] == 1
    with SessionLocal() as db:
        assert db.query(WalletTransaction).filter_by(type=WalletTxnType.REFERRAL_BONUS).count() == 1


def test_cannot_use_own_code_twice_or_after_joining(client, make_person, fake_fpl):
    a, b, code = _setup(client, make_person, fake_fpl)
    assert a.post("/referrals/apply", {"code": code}).status_code == 422          # own code
    assert b.post("/referrals/apply", {"code": "ZZZZZZZZ"}).status_code == 422    # unknown code
    assert b.post("/referrals/apply", {"code": code}).status_code == 204
    assert b.post("/referrals/apply", {"code": code}).status_code == 409          # only once
    c = make_person(103, "+251911000103")
    assert link(c, "2", "Bravo").status_code == 200
    assert c.post("/referrals/apply", {"code": code}).status_code == 409          # already verified/joined


def test_shared_network_holds_reward_until_admin_approves(client, make_person, fake_fpl):
    a, b, code = _setup(client, make_person, fake_fpl)
    with SessionLocal() as db:                                   # simulate both signing in from one network
        from app.models import UserSession
        for s in db.query(UserSession).all():
            s.ip_hash = "same-network"
        db.commit()
    b.post("/referrals/apply", {"code": code})
    link(a, "1", "Alpha"); link(b, "2", "Bravo")
    gw = _open_gameweek()
    _approve(client, _fund(client, b, 500, "TX0002"))
    assert b.post(f"/gameweeks/{gw}/join").status_code == 200
    assert _balance(101) == 0
    with SessionLocal() as db:
        ref = db.query(Referral).one(); assert ref.status == ReferralStatus.HELD; rid = str(ref.id)
    secret = make_admin("ops@example.test", "OPERATIONS_ADMIN")
    csrf = admin_login(client, "ops@example.test", secret)
    assert client.get("/admin-api/referrals").json()[0]["status"] == "HELD"
    assert client.post(f"/admin-api/referrals/{rid}/approve", headers=csrf).status_code == 200
    assert client.post(f"/admin-api/referrals/{rid}/approve", headers=csrf).status_code == 409
    assert _balance(101) == Decimal(settings.referral_reward)


def test_verified_trigger_and_cap(client, make_person, fake_fpl, monkeypatch):
    monkeypatch.setattr(settings, "referral_trigger", "verified")
    a, b, code = _setup(client, make_person, fake_fpl)
    b.post("/referrals/apply", {"code": code}); link(a, "1", "Alpha")
    assert link(b, "2", "Bravo").status_code == 200
    assert _balance(101) == Decimal(settings.referral_reward)


def test_users_cannot_see_who_they_referred(client, make_person, fake_fpl):
    a, b, code = _setup(client, make_person, fake_fpl)
    b.post("/referrals/apply", {"code": code})
    assert set(a.get("/referrals/me").json()) == {"code", "link", "reward", "trigger", "invited", "rewarded", "pending", "earned", "can_apply_code"}
    assert client.get("/admin-api/referrals", headers=a.h).status_code == 401
