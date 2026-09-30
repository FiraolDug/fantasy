from app.config import settings
from app.database import SessionLocal
from app.models import FplTeam, FraudAlert
from tests.conftest import BOT_HEADERS, Person, init_data

ID, NAME = "1234567", "Addis Arsenal"


def link(p, manager=ID, name=NAME):
    assert p.post("/verification/fpl/manager-id", {"manager_id": manager}).status_code == 200
    return p.post("/verification/fpl/team-name", {"team_name": name})


# ---------------------------------------------------------------- Telegram / phone
def test_bad_or_stale_init_data_is_rejected(client):
    bad = init_data(1).replace("hash=", "hash=00")
    assert client.post("/auth/telegram-webapp", json={"init_data": bad}).status_code == 401
    assert client.post("/auth/telegram-webapp", json={"init_data": init_data(1, age=7200)}).status_code == 401


def test_phone_must_belong_to_the_telegram_account(client):
    Person(client, 10)
    r = client.post("/internal/register", headers=BOT_HEADERS, json={
        "telegram_id": "10", "contact_user_id": "99", "phone_number": "+251911000001"})
    assert r.status_code == 400
    r = client.post("/internal/register", headers={"X-Bot-Secret": "wrong"}, json={
        "telegram_id": "10", "contact_user_id": "10", "phone_number": "+251911000001"})
    assert r.status_code == 401


def test_phone_cannot_be_reused_or_swapped(client, make_person):
    make_person(20, "+251911000020")
    Person(client, 21)
    dup = client.post("/internal/register", headers=BOT_HEADERS, json={
        "telegram_id": "21", "contact_user_id": "21", "phone_number": "+251911000020"})
    assert dup.status_code == 409
    swap = client.post("/internal/register", headers=BOT_HEADERS, json={
        "telegram_id": "20", "contact_user_id": "20", "phone_number": "+251911999999"})
    assert swap.status_code == 409


def test_verification_needs_a_verified_phone(client, fake_fpl):
    p = Person(client, 30)                 # no phone
    fake_fpl.teams[ID] = (NAME, "ET")
    assert p.post("/verification/fpl/manager-id", {"manager_id": ID}).status_code == 403


# ---------------------------------------------------------------- happy path and mismatches
def test_happy_path_links_team_and_shows_only_own_data(client, make_person, fake_fpl):
    fake_fpl.teams[ID] = (NAME, "ET")
    p = make_person(40)
    r = p.post("/verification/fpl/manager-id", {"manager_id": ID})
    assert r.json() == {"step": "team_name", "country": "ET"}          # no team data leaks at step 1
    r = p.post("/verification/fpl/team-name", {"team_name": "  Addis   Arsenal "})   # spacing normalised
    assert r.status_code == 200 and r.json()["step"] == "done"
    prof = p.get("/users/me/profile").json()
    assert prof["verified"] and prof["fpl_manager_id"] == ID and prof["fpl_team_name"] == NAME


def test_wrong_name_is_blocked_with_a_clear_message(client, make_person, fake_fpl):
    fake_fpl.teams[ID] = (NAME, "ET")
    p = make_person(41)
    r = link(p, name="addis arsenal")             # case-sensitive by default
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "name_mismatch" and "doesn't match" in d["message"] and d["attempts_left"] == 4
    with SessionLocal() as db:
        assert db.query(FplTeam).count() == 0


def test_not_found_and_non_ethiopian_look_identical(client, make_person, fake_fpl):
    fake_fpl.teams["777"] = ("Foreign FC", "GB")
    p = make_person(42)
    a = p.post("/verification/fpl/manager-id", {"manager_id": "999"})     # doesn't exist
    b = p.post("/verification/fpl/manager-id", {"manager_id": "777"})     # exists, not Ethiopia
    assert a.status_code == b.status_code == 422
    assert a.json()["detail"]["message"] == b.json()["detail"]["message"]


def test_invalid_manager_ids_never_reach_fpl(client, make_person, fake_fpl):
    p = make_person(43)
    for bad in ["abc", "0123", "12 34", "1" * 11, "-5", "1;drop"]:
        assert p.post("/verification/fpl/manager-id", {"manager_id": bad}).status_code == 422


def test_lockout_after_repeated_failures(client, make_person, fake_fpl):
    fake_fpl.teams[ID] = (NAME, "ET")
    p = make_person(44)
    for _ in range(settings.verification_max_failed_per_day):
        assert link(p, name="nope").status_code == 422
    assert p.post("/verification/fpl/manager-id", {"manager_id": ID}).status_code == 429
    assert p.post("/verification/fpl/team-name", {"team_name": NAME}).status_code in (409, 429)  # correct name refused too


def test_manager_id_enumeration_is_capped(client, make_person, fake_fpl):
    p = make_person(45)
    for i in range(settings.verification_max_manager_ids_per_day):
        assert p.post("/verification/fpl/manager-id", {"manager_id": str(500 + i)}).status_code == 422
    assert p.post("/verification/fpl/manager-id", {"manager_id": "600"}).status_code == 429


# ---------------------------------------------------------------- spoofing
def test_second_account_cannot_claim_a_linked_manager(client, make_person, fake_fpl):
    fake_fpl.teams[ID] = (NAME, "ET")
    owner, thief = make_person(50, "+251911000050"), make_person(51, "+251911000051")
    assert link(owner).status_code == 200
    r = link(thief)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_linked"
    with SessionLocal() as db:
        assert db.query(FraudAlert).filter_by(category="manager_id_claim_conflict").count() == 1


def test_ownership_challenge_blocks_someone_who_only_knows_public_facts(client, make_person, fake_fpl, monkeypatch):
    monkeypatch.setattr(settings, "require_ownership_challenge", True)
    fake_fpl.teams[ID] = (NAME, "ET")
    p = make_person(52)
    r = link(p)
    assert r.json()["step"] == "ownership"
    code = r.json()["challenge"]["code"]
    assert p.post("/verification/fpl/ownership").status_code == 422        # impostor can't rename the real team
    assert p.get("/verification/status").json()["verified"] is False
    fake_fpl.teams[ID] = (code, "ET")                                       # true owner renames the team
    done = p.post("/verification/fpl/ownership")
    assert done.status_code == 200 and done.json()["team"]["team_name"] == NAME


# ---------------------------------------------------------------- data isolation
def test_users_only_ever_see_their_own_standing(client, make_person, fake_fpl):
    from app.models import CompetitionEntry, EntryStatus, Gameweek, ScoreSnapshot, utcnow
    from datetime import timedelta
    fake_fpl.teams.update({"1": ("Alpha", "ET"), "2": ("Bravo", "ET")})
    a, b = make_person(60, "+251911000060"), make_person(61, "+251911000061")
    assert link(a, "1", "Alpha").status_code == 200 and link(b, "2", "Bravo").status_code == 200
    with SessionLocal() as db:
        gw = Gameweek(gw_number=1, entry_fee=0, registration_deadline=utcnow() + timedelta(days=1))
        db.add(gw); db.flush()
        for uid_team, pts in (("1", 80), ("2", 95)):
            team = db.query(FplTeam).filter_by(manager_id=uid_team).one()
            e = CompetitionEntry(user_id=team.user_id, gameweek_id=gw.id, fpl_team_id=team.id, status=EntryStatus.CONFIRMED)
            db.add(e); db.flush()
            db.add(ScoreSnapshot(competition_entry_id=e.id, points=pts, rank=1 if pts == 95 else 2))
        db.commit(); gwid = str(gw.id)
    rows = a.get(f"/gameweeks/{gwid}/leaderboard").json()
    assert rows == [{"rank": 2, "display_name": "Alpha", "points": 80, "is_me": True}]
    assert "Bravo" not in a.get(f"/gameweeks/{gwid}/leaderboard").text


def test_old_lookup_endpoints_are_gone(client):
    assert client.post("/fpl/lookup", json={"manager_id": "1"}).status_code == 404
    assert client.post("/internal/fpl-confirm", json={}, headers=BOT_HEADERS).status_code == 404


def test_unauthenticated_and_user_tokens_cannot_reach_admin(client, make_person):
    p = make_person(70)
    assert client.get("/users/me/profile").status_code == 401
    assert client.get("/admin-api/users").status_code == 401
    assert client.get("/admin-api/users", headers=p.h).status_code == 401
