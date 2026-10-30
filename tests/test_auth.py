"""Sign-in, sessions, lockout and CSRF."""

import sqlite3

from fastapi.testclient import TestClient

from app.domain.users import hash_password, verify_password
from tests.constants import ADMIN, PASSWORD


def test_password_hash_roundtrip():
    stored = hash_password("a-long-enough-password")

    assert stored.startswith("scrypt$")
    assert "a-long-enough-password" not in stored
    assert verify_password("a-long-enough-password", stored)
    assert not verify_password("a-long-enough-passworD", stored)
    assert hash_password("same") != hash_password("same")  # per-user random salt


def test_login_sets_hardened_session_cookie(client):  # AU1
    r = client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})

    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/api/auth/me").json()["username"] == ADMIN


def test_bad_password_and_unknown_user_look_identical(client):  # AU2
    bad = client.post("/api/auth/login", json={"username": ADMIN, "password": "wrong-password"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever"})

    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()


def test_lockout_after_five_failures(client, clock):  # AU3
    for _ in range(5):
        client.post("/api/auth/login", json={"username": ADMIN, "password": "wrong-password"})

    locked = client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
    assert locked.status_code == 401  # even the right password, and no hint that it's locked

    clock.advance(15 * 60 + 1)
    assert (
        client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD}).status_code
        == 200
    )


def test_session_expires(admin_client, clock):  # AU4
    assert admin_client.get("/api/auth/me").status_code == 200

    clock.advance(8 * 3600 + 1)

    r = admin_client.get("/api/auth/me")
    assert (r.status_code, r.json()["error"]["code"]) == (401, "not_authenticated")


def test_logout_ends_session(admin_client):  # AU5
    cookie = dict(admin_client.cookies)
    assert admin_client.post("/api/auth/logout").status_code == 204

    assert admin_client.get("/api/auth/me").status_code == 401
    # Replaying the old cookie doesn't help: the server-side session is gone.
    admin_client.cookies.update(cookie)
    assert admin_client.get("/api/auth/me").status_code == 401


def test_unauthenticated_requests_are_401(client):
    assert client.get("/api/samples").status_code == 401
    assert client.post("/api/samples", json={"name": "x"}).status_code == 401


def test_requests_without_csrf_header_are_rejected(app, admin_client):  # AU6
    no_header = TestClient(app)
    no_header.cookies.update(dict(admin_client.cookies))

    r = no_header.post("/api/samples", json={"name": "x"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_check_failed")
    login = no_header.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
    assert login.status_code == 403  # login CSRF is blocked too
    assert no_header.get("/api/samples").status_code == 200  # safe methods are fine


def test_deactivated_user_is_signed_out(admin_client, login_as, api_users):  # AU7
    alice = login_as("alice")
    assert alice.get("/api/samples").status_code == 200

    admin_client.patch(f"/api/admin/users/{api_users['alice']}", json={"is_active": False})

    assert alice.get("/api/samples").status_code == 401
    r = alice.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 401


def test_old_failures_expire_instead_of_adding_up(client, clock):
    for _ in range(4):
        client.post("/api/auth/login", json={"username": ADMIN, "password": "wrong-password"})
    clock.advance(16 * 60)  # outside the 15-minute window
    client.post("/api/auth/login", json={"username": ADMIN, "password": "wrong-password"})

    r = client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
    assert r.status_code == 200  # 1 recent failure, not 5: no lockout


def test_logout_after_expiry_still_clears_the_cookie(admin_client, clock):
    clock.advance(8 * 3600 + 1)

    r = admin_client.post("/api/auth/logout")
    assert r.status_code == 204
    assert "sfa_session=" in r.headers["set-cookie"]  # deleted (Max-Age=0)


def test_expired_sessions_are_purged_on_login(admin_client, login_as, clock, settings):
    clock.advance(8 * 3600 + 1)
    login_as(ADMIN)  # a fresh login cleans up expired rows

    conn = sqlite3.connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    conn.close()
