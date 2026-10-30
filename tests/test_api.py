"""HTTP tests: wiring, status codes and response shapes. The rules themselves are in
test_access_service.py."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.service import AccessService
from tests.constants import CSRF

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def alice(login_as, api_users):
    return login_as("alice")


@pytest.fixture
def bob(login_as, api_users):
    return login_as("bob")


@pytest.fixture
def mallory(login_as, api_users):
    return login_as("mallory")


@pytest.fixture
def sample(alice):
    r = alice.post("/api/samples", json={"name": "TCGA-0001", "files": [{"filename": "t.bam"}]})
    assert r.status_code == 201
    return r.json()


def _qc(client, file_id, status, headers):
    return client.put(
        f"/api/internal/files/{file_id}/qc-status", json={"status": status}, headers=headers
    )


def test_happy_path_end_to_end(alice, bob, pipeline):  # A1
    r = alice.post(
        "/api/samples",
        json={"name": "TCGA-0001", "files": [{"filename": "tumor.bam"}, {"filename": "n.bam"}]},
    )
    assert r.status_code == 201
    body = r.json()
    assert r.headers["Location"] == f"/api/samples/{body['id']}"
    assert [f["qc_status"] for f in body["files"]] == ["pending", "pending"]
    file_id = body["files"][0]["id"]

    r = alice.post(f"/api/samples/{body['id']}/grants", json={"username": "bob"})
    assert (r.status_code, r.json()["username"]) == (201, "bob")
    r = alice.post(f"/api/samples/{body['id']}/grants", json={"username": "bob"})
    assert r.status_code == 200  # idempotent

    r = _qc(alice, file_id, "passed", pipeline)
    assert (r.status_code, r.json()["changed"]) == (200, True)

    r = bob.post(f"/api/files/{file_id}/download-url")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"

    r = bob.get(r.json()["url"])
    assert r.status_code == 200


def test_listing_reflects_access(alice, bob, sample):
    assert [s["access"] for s in alice.get("/api/samples").json()] == ["owner"]
    assert bob.get("/api/samples").json() == []

    alice.post(f"/api/samples/{sample['id']}/grants", json={"username": "bob"})

    assert [s["access"] for s in bob.get("/api/samples").json()] == ["grantee"]
    assert alice.get(f"/api/samples/{sample['id']}/grants").json()[0]["username"] == "bob"


def test_stranger_and_unknown_file_get_identical_404(sample, mallory, alice, pipeline):  # A3
    file_id = sample["files"][0]["id"]
    _qc(alice, file_id, "passed", pipeline)

    stranger = mallory.post(f"/api/files/{file_id}/download-url")
    unknown = mallory.post(f"/api/files/{UNKNOWN_ID}/download-url")

    assert stranger.status_code == unknown.status_code == 404
    assert stranger.json() == unknown.json()
    assert mallory.get(f"/api/samples/{sample['id']}").status_code == 404


def test_pending_and_failed_files_are_409_for_owner(alice, sample, pipeline):  # A4
    file_id = sample["files"][0]["id"]

    r = alice.post(f"/api/files/{file_id}/download-url")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "qc_pending")

    _qc(alice, file_id, "failed", pipeline)
    r = alice.post(f"/api/files/{file_id}/download-url")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "qc_failed")


def test_qc_callback_auth_validation_and_transitions(client, sample, pipeline):  # A5
    file_id = sample["files"][0]["id"]
    wrong = {"X-Pipeline-Token": "wrong"}

    assert _qc(client, file_id, "passed", {}).status_code == 401
    assert _qc(client, file_id, "passed", wrong).status_code == 401
    assert _qc(client, file_id, "pending", pipeline).status_code == 422

    assert _qc(client, file_id, "passed", pipeline).status_code == 200
    r = _qc(client, file_id, "passed", pipeline)
    assert (r.status_code, r.json()["changed"]) == (200, False)
    r = _qc(client, file_id, "failed", pipeline)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "invalid_transition")

    assert _qc(client, UNKNOWN_ID, "passed", pipeline).status_code == 404


def test_pipeline_callback_needs_no_csrf_header_but_browsers_do(app, sample, pipeline):
    # A real pipeline sends only its token: no cookie, no X-Requested-With.
    bare = TestClient(app)
    file_id = sample["files"][0]["id"]

    r = bare.put(
        f"/api/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline
    )
    assert r.status_code == 200
    r = bare.post("/api/samples", json={"name": "x"})  # any cookie-authenticated route
    assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_check_failed")


def test_expired_or_tampered_link_is_403(alice, sample, pipeline, clock, api_users):  # A6
    file_id = sample["files"][0]["id"]
    _qc(alice, file_id, "passed", pipeline)
    url = alice.post(f"/api/files/{file_id}/download-url").json()["url"]

    tampered = alice.get(url.replace(api_users["alice"], api_users["bob"]))
    assert tampered.status_code == 403

    clock.advance(301)
    expired = alice.get(url)
    assert expired.status_code == 403
    assert expired.json() == tampered.json()  # same body: caller can't tell why


def test_grant_permissions(alice, bob, mallory, sample):
    path = f"/api/samples/{sample['id']}/grants"
    alice.post(path, json={"username": "bob"})

    assert bob.post(path, json={"username": "mallory"}).status_code == 403
    assert mallory.post(path, json={"username": "mallory"}).status_code == 404
    assert alice.post(path, json={"username": "alice"}).status_code == 400
    r = alice.post(path, json={"username": "nobody"})
    assert (r.status_code, r.json()["error"]["code"]) == (404, "user_not_found")


def test_owner_deletes_file_and_sample(alice, bob, sample):
    alice.post(f"/api/samples/{sample['id']}/grants", json={"username": "bob"})
    file_id = sample["files"][0]["id"]

    assert bob.delete(f"/api/files/{file_id}").status_code == 403
    assert alice.delete(f"/api/files/{file_id}").status_code == 204
    assert alice.get(f"/api/samples/{sample['id']}").json()["files"] == []
    assert alice.delete(f"/api/samples/{sample['id']}").status_code == 204
    assert alice.get(f"/api/samples/{sample['id']}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "s", "files": [{"filename": "a.bam"}, {"filename": "a.bam"}]},  # duplicate
        {"name": "s", "files": [{"filename": "../etc/passwd"}]},  # path separator
        {"name": "", "files": []},  # empty name
        {"name": "s", "owner_id": "mallory"},  # unknown field (owner comes from the session)
    ],
)
def test_invalid_registration_is_422(alice, body):  # A7
    assert alice.post("/api/samples", json=body).status_code == 422


def test_healthz_and_unknown_api_route(client):
    assert client.get("/api/healthz").json() == {"status": "ok"}
    assert client.get("/api/does-not-exist").status_code == 404


def test_framework_errors_use_the_same_error_shape(client, alice):
    r = alice.post("/api/samples", json={"name": "", "extra": 1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "name" in r.json()["error"]["message"]
    assert all("input" not in d for d in r.json()["error"]["details"])  # no echo of user input

    r = client.get("/api/auth/login")  # wrong method (no UI mounted in this app)
    assert (r.status_code, r.json()["error"]["code"]) == (405, "method_not_allowed")
    assert r.headers["allow"] == "POST"


def test_malformed_or_odd_requests_never_500(client, alice, sample, pipeline, settings):
    file_id = sample["files"][0]["id"]
    # A download link with a non-UUID user is rejected before anything is audited.
    r = client.get(f"/api/downloads/{file_id}", params={"user": "x" * 500, "exp": 1, "sig": "a"})
    assert r.status_code == 422

    # Bytes missing from storage: a clean 404, not a crash.
    up = alice.post(
        f"/api/samples/{sample['id']}/uploads", json={"filename": "b.bin", "size_bytes": 3}
    ).json()
    alice.put(f"/api/uploads/{up['id']}", params={"offset": 0}, content=b"abc")
    new_id = alice.post(f"/api/uploads/{up['id']}/complete").json()["id"]
    _qc(alice, new_id, "passed", pipeline)
    url = alice.post(f"/api/files/{new_id}/download-url").json()["url"]
    (Path(settings.storage_dir) / new_id).unlink()
    r = alice.get(url)
    assert (r.status_code, r.json()["error"]["code"]) == (404, "file_not_found")
    events = [e["event"] for e in admin_events(settings)]
    assert events[0] != "download_served"  # a failed download isn't recorded as served


def test_chunk_for_an_upload_whose_bytes_vanished_is_404(alice, sample, settings):
    up = alice.post(
        f"/api/samples/{sample['id']}/uploads", json={"filename": "c.bin", "size_bytes": 3}
    ).json()
    (Path(settings.storage_dir) / f"{up['id']}.part").unlink()  # e.g. aborted in another tab
    r = alice.put(f"/api/uploads/{up['id']}", params={"offset": 0}, content=b"abc")
    assert r.status_code == 404


def admin_events(settings):
    conn = sqlite3.connect(settings.db_path)
    rows = conn.execute("SELECT event FROM audit_events ORDER BY id DESC").fetchall()
    conn.close()
    return [{"event": r[0]} for r in rows]


def test_head_on_a_download_link_is_rejected_and_not_audited(
    client, alice, sample, pipeline, settings
):
    file_id = sample["files"][0]["id"]
    _qc(alice, file_id, "passed", pipeline)
    url = alice.post(f"/api/files/{file_id}/download-url").json()["url"]

    assert client.head(url).status_code == 405  # link scanners send HEAD
    assert "download_served" not in [e["event"] for e in admin_events(settings)]


def test_bad_json_and_unexpected_errors_keep_the_error_shape(app, alice, monkeypatch):
    r = alice.post("/api/samples", content=b"{bad", headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["error"]["message"]) == (422, "JSON decode error")

    def boom(*_):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(AccessService, "list_samples", boom)
    crashing = TestClient(app, headers=CSRF, raise_server_exceptions=False)
    crashing.cookies.update(dict(alice.cookies))
    r = crashing.get("/api/samples")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "internal server error"}}
