"""App wiring: serving the UI, security headers, API docs toggle, admin seeding."""

import dataclasses
import uuid

import pytest
from fastapi.testclient import TestClient

from app.domain.models import Role, User
from app.domain.users import hash_password
from app.main import create_app
from app.persistence.repository import SqliteRepository, connect
from tests.constants import CSRF


@pytest.fixture
def spa_client(settings, tmp_path, clock):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside dist")
    return TestClient(create_app(dataclasses.replace(settings, frontend_dist=str(dist)), clock))


def test_serves_ui_files_and_client_routes(spa_client):
    assert spa_client.get("/").text == "<html>app</html>"
    assert spa_client.get("/samples/123").text == "<html>app</html>"  # client-side route
    assert spa_client.get("/assets/app.js").text == "console.log(1)"
    assert "outside dist" not in spa_client.get("/../secret.txt").text
    csp = spa_client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp


def test_api_paths_never_fall_through_to_the_ui(spa_client):
    r = spa_client.get("/api/nope")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    r = spa_client.get("/api/auth/login")  # exists, but only as POST
    assert (r.status_code, r.headers["allow"]) == (405, "POST")
    assert spa_client.get("/%00").status_code == 404  # NUL byte: no crash


def test_security_headers_on_every_response(app, client):
    rejected = TestClient(app).post("/api/samples", json={})  # CSRF rejection, too
    for headers in (client.get("/api/healthz").headers, rejected.headers):
        assert headers["x-frame-options"] == "DENY"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"


def test_head_and_405_are_consistent_with_or_without_the_ui(client, spa_client):
    assert client.head("/api/healthz").status_code == 200
    assert spa_client.head("/").status_code == 200
    for c in (client, spa_client):
        r = c.head("/api/samples")  # GET-only API routes: an honest 405 listing every method
        assert (r.status_code, r.headers["allow"]) == (405, "GET, POST")
        r = c.patch("/api/samples", headers=CSRF)
        assert (r.status_code, r.headers["allow"]) == (405, "GET, POST")


def test_api_docs_can_be_disabled(settings, clock):
    assert TestClient(create_app(settings, clock)).get("/api/docs").status_code == 200
    off = create_app(dataclasses.replace(settings, enable_docs=False), clock)
    assert TestClient(off).get("/api/docs").status_code == 404


def test_admin_seed_collision_fails_with_a_clear_message(settings, clock):
    # A database where "admin" already exists as a plain user, and no admin exists yet.
    create_app(dataclasses.replace(settings, admin_username="", admin_password=""), clock)
    conn = connect(settings.db_path)
    SqliteRepository(conn).add_user(
        User(
            id=str(uuid.uuid4()),
            username="admin",
            role=Role.USER,
            is_active=True,
            created_at=clock.now(),
            password_hash=hash_password("correct-horse-battery"),
        )
    )
    conn.close()

    with pytest.raises(RuntimeError, match="cannot create the first admin 'admin'"):
        create_app(settings, clock)
