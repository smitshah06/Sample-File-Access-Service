"""HTTP tests: wiring, status codes and response shapes. The rules themselves are in
test_access_service.py."""

import pytest

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def _as(user):
    return {"X-User-Id": user}


@pytest.fixture
def pipeline(settings):
    return {"X-Pipeline-Token": settings.qc_callback_token}


@pytest.fixture
def sample(client):
    r = client.post(
        "/samples",
        json={"name": "TCGA-0001", "files": [{"filename": "tumor.bam"}]},
        headers=_as("alice"),
    )
    assert r.status_code == 201
    return r.json()


def test_happy_path_end_to_end(client, pipeline):  # A1
    r = client.post(
        "/samples",
        json={"name": "TCGA-0001", "files": [{"filename": "tumor.bam"}, {"filename": "n.bam"}]},
        headers=_as("alice"),
    )
    assert r.status_code == 201
    body = r.json()
    assert r.headers["Location"] == f"/samples/{body['id']}"
    assert [f["qc_status"] for f in body["files"]] == ["pending", "pending"]
    file_id = body["files"][0]["id"]

    r = client.post(f"/samples/{body['id']}/grants", json={"user_id": "bob"}, headers=_as("alice"))
    assert r.status_code == 201
    r = client.post(f"/samples/{body['id']}/grants", json={"user_id": "bob"}, headers=_as("alice"))
    assert r.status_code == 200  # idempotent

    r = client.put(
        f"/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline
    )
    assert r.status_code == 200
    assert r.json()["changed"] is True

    r = client.post(f"/files/{file_id}/download-url", headers=_as("bob"))
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    url = r.json()["url"]

    r = client.get(url)
    assert r.status_code == 200
    assert r.json()["file_id"] == file_id


def test_missing_identity_is_401(client, sample):  # A2
    r = client.post(f"/files/{sample['files'][0]['id']}/download-url")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "missing_identity"

    r = client.post("/samples", json={"name": "x"}, headers=_as("not a valid id!"))
    assert r.status_code == 401


def test_stranger_and_unknown_file_get_identical_404(client, sample, pipeline):  # A3
    file_id = sample["files"][0]["id"]
    client.put(f"/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline)

    stranger = client.post(f"/files/{file_id}/download-url", headers=_as("mallory"))
    unknown = client.post(f"/files/{UNKNOWN_ID}/download-url", headers=_as("mallory"))

    assert stranger.status_code == unknown.status_code == 404
    assert stranger.json() == unknown.json()


def test_pending_and_failed_files_are_409_for_owner(client, sample, pipeline):  # A4
    file_id = sample["files"][0]["id"]

    r = client.post(f"/files/{file_id}/download-url", headers=_as("alice"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "qc_pending"

    client.put(f"/internal/files/{file_id}/qc-status", json={"status": "failed"}, headers=pipeline)
    r = client.post(f"/files/{file_id}/download-url", headers=_as("alice"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "qc_failed"


def test_qc_callback_auth_validation_and_transitions(client, sample, pipeline):  # A5
    url = f"/internal/files/{sample['files'][0]['id']}/qc-status"

    assert client.put(url, json={"status": "passed"}).status_code == 401
    assert (
        client.put(
            url, json={"status": "passed"}, headers={"X-Pipeline-Token": "wrong"}
        ).status_code
        == 401
    )
    assert client.put(url, json={"status": "pending"}, headers=pipeline).status_code == 422

    assert client.put(url, json={"status": "passed"}, headers=pipeline).status_code == 200
    r = client.put(url, json={"status": "passed"}, headers=pipeline)
    assert (r.status_code, r.json()["changed"]) == (200, False)
    r = client.put(url, json={"status": "failed"}, headers=pipeline)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "invalid_transition")

    unknown = f"/internal/files/{UNKNOWN_ID}/qc-status"
    assert client.put(unknown, json={"status": "passed"}, headers=pipeline).status_code == 404


def test_expired_or_tampered_link_is_403(client, sample, pipeline, clock):  # A6
    file_id = sample["files"][0]["id"]
    client.put(f"/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline)
    url = client.post(f"/files/{file_id}/download-url", headers=_as("alice")).json()["url"]

    tampered = client.get(url.replace("user=alice", "user=mallory"))
    assert tampered.status_code == 403

    clock.advance(301)
    expired = client.get(url)
    assert expired.status_code == 403
    assert expired.json() == tampered.json()  # same body: caller can't tell why


def test_grant_permissions(client, sample):
    path = f"/samples/{sample['id']}/grants"
    client.post(path, json={"user_id": "bob"}, headers=_as("alice"))

    assert client.post(path, json={"user_id": "carol"}, headers=_as("bob")).status_code == 403
    assert client.post(path, json={"user_id": "carol"}, headers=_as("mallory")).status_code == 404
    assert client.post(path, json={"user_id": "alice"}, headers=_as("alice")).status_code == 400


def test_get_sample_visibility(client, sample):
    path = f"/samples/{sample['id']}"
    assert client.get(path, headers=_as("alice")).json()["id"] == sample["id"]
    assert client.get(path, headers=_as("mallory")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "s", "files": [{"filename": "a.bam"}, {"filename": "a.bam"}]},  # duplicate
        {"name": "s", "files": [{"filename": "../etc/passwd"}]},  # path separator
        {"name": "", "files": []},  # empty name
        {"name": "s", "owner_id": "mallory"},  # unknown field (owner comes from the caller)
    ],
)
def test_invalid_registration_is_422(client, body):  # A7
    assert client.post("/samples", json=body, headers=_as("alice")).status_code == 422


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
