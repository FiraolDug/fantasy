import uuid

from app.database import SessionLocal
from app.models import AuditLog, Wallet
from tests.conftest import admin_login, make_admin

from tests.test_verification import ID, NAME, link


def test_admin_login_needs_password_and_totp(client):
    secret = make_admin()
    bad_pw = client.post("/admin-api/auth/login", json={"email": "root@example.test", "password": "wrong", "code": "000000"})
    no_code = client.post("/admin-api/auth/login", json={"email": "root@example.test", "password": "Str0ngPassw0rd!x", "code": ""})
    unknown = client.post("/admin-api/auth/login", json={"email": "nobody@example.test", "password": "x", "code": "000000"})
    assert bad_pw.status_code == no_code.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json()                      # no account enumeration
    csrf = admin_login(client, "root@example.test", secret)
    assert client.get("/admin-api/auth/me").json()["role"] == "SUPER_ADMIN"
    cookie = client.cookies.jar
    assert any(c.name == "admin_session" and c.has_nonstandard_attr("HttpOnly") for c in cookie)


def test_account_locks_after_repeated_failures(client):
    secret = make_admin()
    for _ in range(5):
        client.post("/admin-api/auth/login", json={"email": "root@example.test", "password": "bad", "code": "000000"})
    import pyotp
    r = client.post("/admin-api/auth/login", json={"email": "root@example.test", "password": "Str0ngPassw0rd!x",
                                                   "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 401                                 # correct credentials still refused while locked


def test_csrf_token_is_required_for_changes(client):
    secret = make_admin()
    csrf = admin_login(client, "root@example.test", secret)
    body = {"title": "Deadline moved", "body": "Text", "status": "DRAFT"}
    assert client.post("/admin-api/posts", json=body).status_code == 403
    assert client.post("/admin-api/posts", json=body, headers=csrf).status_code == 201


def test_editor_can_draft_but_not_publish_and_everything_is_audited(client):
    make_admin()
    ed_secret = make_admin("editor@example.test", "CONTENT_EDITOR")
    csrf = admin_login(client, "editor@example.test", ed_secret)
    assert client.post("/admin-api/posts", json={"title": "Draft post", "body": "x", "status": "PUBLISHED"}, headers=csrf).status_code == 403
    post = client.post("/admin-api/posts", json={"title": "Draft post", "body": "x"}, headers=csrf).json()
    assert client.patch(f"/admin-api/posts/{post['id']}", json={"status": "PUBLISHED"}, headers=csrf).status_code == 403
    assert client.get("/admin-api/deposits", headers=csrf).status_code == 403          # no finance access
    assert client.get("/admin-api/audit-log", headers=csrf).status_code == 403
    assert client.get("/admin-api/admins", headers=csrf).status_code == 403
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="post.create").count() == 1


def test_published_posts_reach_users_and_drafts_do_not(client, make_person):
    secret = make_admin()
    csrf = admin_login(client, "root@example.test", secret)
    client.post("/admin-api/posts", json={"title": "Live news", "body": "hello", "status": "PUBLISHED"}, headers=csrf)
    client.post("/admin-api/posts", json={"title": "Secret draft", "body": "no"}, headers=csrf)
    titles = [p["title"] for p in make_person(80).get("/posts").json()]
    assert titles == ["Live news"]


def test_admin_management_guards(client):
    secret = make_admin()
    csrf = admin_login(client, "root@example.test", secret)
    me = client.get("/admin-api/auth/me").json()["id"]
    assert client.patch(f"/admin-api/admins/{me}", json={"status": "DISABLED"}, headers=csrf).status_code == 403
    weak = client.post("/admin-api/admins", headers=csrf, json={"email": "a@b.co", "full_name": "Ab", "role": "FINANCE_ADMIN", "password": "short"})
    assert weak.status_code == 422
    ok = client.post("/admin-api/admins", headers=csrf, json={"email": "fin@b.co", "full_name": "Fin Admin", "role": "FINANCE_ADMIN", "password": "Longer-Passw0rd-1"})
    assert ok.status_code == 201 and ok.json()["totp_uri"].startswith("otpauth://")


def test_deposit_duplicate_receipt_rejected_and_credit_is_idempotent(client, make_person, fake_fpl):
    fake_fpl.teams[ID] = (NAME, "ET")
    u = make_person(90); assert link(u).status_code == 200
    body = {"method": "TELEBIRR", "amount": "300", "transaction_id": "ab12cd34"}
    first = u.post("/deposits", body)
    assert first.status_code == 201
    assert u.post("/deposits", body).status_code == 409
    secret = make_admin("fin@example.test", "FINANCE_ADMIN")
    csrf = admin_login(client, "fin@example.test", secret)
    dep = first.json()["id"]
    assert client.post(f"/admin-api/deposits/{dep}/approve", headers=csrf).status_code == 200
    assert client.post(f"/admin-api/deposits/{dep}/approve", headers=csrf).status_code == 409
    with SessionLocal() as db:
        assert str(db.query(Wallet).one().available_balance) in ("300", "300.00")


def test_withdrawal_is_idempotent_and_cannot_overdraw(client, make_person, fake_fpl):
    fake_fpl.teams[ID] = (NAME, "ET")
    u = make_person(91); link(u)
    secret = make_admin("fin@example.test", "FINANCE_ADMIN")
    csrf = admin_login(client, "fin@example.test", secret)
    dep = u.post("/deposits", {"method": "CBE", "amount": "500", "transaction_id": "FT2600001"}).json()["id"]
    client.post(f"/admin-api/deposits/{dep}/approve", headers=csrf)
    key = uuid.uuid4().hex
    req = {"amount": "200", "destination_account": "0911223344"}
    a = u.post("/withdrawals", req, headers={"Idempotency-Key": key})
    b = u.post("/withdrawals", req, headers={"Idempotency-Key": key})       # double tap / retry
    assert a.status_code == 201 and b.json()["id"] == a.json()["id"]
    assert u.post("/withdrawals", {"amount": "400", "destination_account": "0911223344"},
                  headers={"Idempotency-Key": uuid.uuid4().hex}).status_code == 402
    assert u.post("/withdrawals", req).status_code == 422                 # header is mandatory
    with SessionLocal() as db:
        w = db.query(Wallet).one()
        assert (float(w.available_balance), float(w.pending_balance)) == (300.0, 200.0)
    assert client.post(f"/admin-api/withdrawals/{a.json()['id']}/reject", headers=csrf, json={"reason": "wrong account"}).status_code == 200
    with SessionLocal() as db:
        w = db.query(Wallet).one()
        assert (float(w.available_balance), float(w.pending_balance)) == (500.0, 0.0)


def test_unverified_users_cannot_touch_money(client, make_person):
    u = make_person(92)
    assert u.post("/deposits", {"method": "CBE", "amount": "100", "transaction_id": "ABC123"}).status_code == 403
