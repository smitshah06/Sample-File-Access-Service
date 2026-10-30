"""Admin-only user management, sample overview and audit log."""

import pytest

from tests.constants import PASSWORD


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/admin/users"),
        ("post", "/api/admin/users"),
        ("get", "/api/admin/samples"),
        ("get", "/api/admin/audit"),
    ],
)
def test_users_cannot_reach_admin_routes(login_as, api_users, method, path):  # AD1
    alice = login_as("alice")
    kwargs = {"json": {"username": "eve", "password": PASSWORD}} if method == "post" else {}

    r = getattr(alice, method)(path, **kwargs)

    assert (r.status_code, r.json()["error"]["code"]) == (403, "admin_required")


def test_admin_creates_users(admin_client, api_users):  # AD2
    names = [u["username"] for u in admin_client.get("/api/admin/users").json()]
    assert names == ["admin", "alice", "bob", "mallory"]

    dup = admin_client.post("/api/admin/users", json={"username": "ALICE", "password": PASSWORD})
    assert (dup.status_code, dup.json()["error"]["code"]) == (409, "username_taken")
    weak = admin_client.post("/api/admin/users", json={"username": "eve", "password": "short"})
    assert weak.status_code == 422


def test_role_change_takes_effect_and_forces_new_sign_in(admin_client, login_as, api_users):
    # AD3
    alice = login_as("alice")
    assert alice.get("/api/admin/users").status_code == 403

    r = admin_client.patch(f"/api/admin/users/{api_users['alice']}", json={"role": "admin"})
    assert r.json()["role"] == "admin"

    assert alice.get("/api/auth/me").status_code == 401  # old session ended
    assert login_as("alice").get("/api/admin/users").status_code == 200


def test_admin_cannot_change_themselves(admin_client):  # AD4
    me = admin_client.get("/api/auth/me").json()

    r = admin_client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"})
    assert (r.status_code, r.json()["error"]["code"]) == (400, "self_update")


def test_admin_manages_sharing_on_any_sample(admin_client, login_as, api_users, pipeline):
    # AD5
    alice = login_as("alice")
    bob = login_as("bob")
    sample = alice.post("/api/samples", json={"name": "s", "files": [{"filename": "a.bam"}]})
    sample_id, file_id = sample.json()["id"], sample.json()["files"][0]["id"]
    alice.put(
        f"/api/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline
    )

    assert [s["id"] for s in admin_client.get("/api/admin/samples").json()] == [sample_id]
    r = admin_client.post(f"/api/samples/{sample_id}/grants", json={"username": "bob"})
    assert r.status_code == 201
    url = bob.post(f"/api/files/{file_id}/download-url").json()["url"]

    r = admin_client.delete(f"/api/samples/{sample_id}/grants/{api_users['bob']}")
    assert r.status_code == 204
    assert bob.get(url).status_code == 404  # the already-issued URL stopped working
    assert bob.get("/api/samples").json() == []

    # Admin can see the sample but has no download right of its own.
    assert admin_client.get(f"/api/samples/{sample_id}").json()["access"] == "none"
    assert admin_client.post(f"/api/files/{file_id}/download-url").status_code == 404


def test_audit_log_lists_and_filters_events(admin_client, login_as, api_users):  # AD6
    login_as("alice").post("/api/samples", json={"name": "s"})

    events = admin_client.get("/api/admin/audit").json()
    assert {"user_created", "login_succeeded", "sample_registered"} <= {e["event"] for e in events}
    assert events == sorted(events, key=lambda e: e["id"], reverse=True)  # newest first

    only = admin_client.get("/api/admin/audit", params={"event": "user_created"}).json()
    assert len(only) == 4  # the seeded admin + alice, bob, mallory
    assert {e["event"] for e in only} == {"user_created"}


def test_deactivation_also_kills_download_urls_already_issued(
    admin_client, login_as, api_users, pipeline
):
    alice = login_as("alice")
    sample = alice.post("/api/samples", json={"name": "s", "files": [{"filename": "a.bam"}]})
    file_id = sample.json()["files"][0]["id"]
    alice.put(
        f"/api/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline
    )
    url = alice.post(f"/api/files/{file_id}/download-url").json()["url"]
    assert alice.get(url).status_code == 200

    admin_client.patch(f"/api/admin/users/{api_users['alice']}", json={"is_active": False})

    r = alice.get(url)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "invalid_or_expired_link")
