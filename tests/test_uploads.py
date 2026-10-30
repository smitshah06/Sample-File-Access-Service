"""Resumable chunked uploads of any file type, and downloading real content."""

import hashlib
from urllib.parse import urlparse

import pytest

from app.domain.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    InsufficientStorage,
    NotFound,
    PayloadTooLarge,
)
from app.storage import LocalDiskStore
from tests.constants import CHUNK_BYTES, MAX_UPLOAD_BYTES

CONTENT = bytes(range(256)) * 3  # 768 bytes of binary data: 8 chunks of <=100 bytes


def _send(service, caller, upload, data, start=0):
    offset = start
    while offset < len(data):
        chunk = data[offset : offset + CHUNK_BYTES]
        upload = service.write_chunk(caller, upload.id, offset, chunk)
        offset = upload.received_bytes
    return upload


def _upload(service, caller, sample_id, data=CONTENT, name="reads.bin", content_type=None):
    up = service.start_upload(caller, sample_id, name, len(data), content_type)
    _send(service, caller, up, data)
    return service.complete_upload(caller, up.id)


# --- domain ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("interview.mp4", "video/mp4"),
        (
            "slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ("voice.wav", "audio/wav"),
        ("report.pdf", "application/pdf"),
        ("counts.csv", "text/csv"),
        ("notes.txt", "text/plain"),
        ("tumor.bam", None),
        ("no-extension", ""),
    ],
)
def test_any_file_type_uploads_with_checksum(
    service, make_sample, alice, store, name, content_type
):  # U1
    s = make_sample(files=())

    f = _upload(service, alice, s.id, name=name, content_type=content_type)

    assert f.size_bytes == len(CONTENT)
    assert f.sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert f.qc_status == "pending"
    assert f.content_type == (content_type or "application/octet-stream")
    assert store.path(f.storage_key).read_bytes() == CONTENT
    assert f.storage_key == f.id  # the client's filename is never used on disk


def test_size_cap_is_checked_before_any_bytes_are_sent(service, make_sample, alice, store):
    # U2
    s = make_sample(files=())

    with pytest.raises(PayloadTooLarge):
        service.start_upload(alice, s.id, "huge.mp4", MAX_UPLOAD_BYTES + 1, "video/mp4")

    assert list(store.root.iterdir()) == []


def test_refuses_upload_that_would_fill_the_disk(service, make_sample, alice, monkeypatch):
    s = make_sample(files=())
    monkeypatch.setattr(LocalDiskStore, "free_bytes", lambda self: 500)

    with pytest.raises(InsufficientStorage):
        service.start_upload(alice, s.id, "a.bin", 600, None)


def test_interrupted_upload_resumes_from_last_byte(service, make_sample, alice):  # U3
    s = make_sample(files=())
    up = service.start_upload(alice, s.id, "big.bin", len(CONTENT), None)
    up = service.write_chunk(alice, up.id, 0, CONTENT[:100])
    up = service.write_chunk(alice, up.id, 100, CONTENT[100:200])
    # ... connection drops; the client asks where to resume ...

    assert service.get_upload(alice, up.id).received_bytes == 200
    with pytest.raises(Conflict) as exc:  # a stale retry of an earlier chunk is refused
        service.write_chunk(alice, up.id, 100, CONTENT[100:200])
    assert exc.value.code == "offset_mismatch"

    _send(service, alice, up, CONTENT, start=200)
    f = service.complete_upload(alice, up.id)
    assert f.sha256 == hashlib.sha256(CONTENT).hexdigest()


def test_chunk_rules(service, make_sample, alice):
    s = make_sample(files=())
    up = service.start_upload(alice, s.id, "a.bin", 150, None)

    with pytest.raises(BadRequest):  # bigger than the chunk size
        service.write_chunk(alice, up.id, 0, b"x" * (CHUNK_BYTES + 1))
    with pytest.raises(BadRequest):  # empty
        service.write_chunk(alice, up.id, 0, b"")
    service.write_chunk(alice, up.id, 0, b"x" * 100)
    with pytest.raises(BadRequest):  # past the declared size
        service.write_chunk(alice, up.id, 100, b"x" * 51)
    with pytest.raises(Conflict) as exc:  # not finished yet
        service.complete_upload(alice, up.id)
    assert exc.value.code == "upload_incomplete"


def test_completing_twice_or_after_lost_bytes_is_404_not_500(service, make_sample, alice, store):
    s = make_sample(files=())
    up = service.start_upload(alice, s.id, "a.bin", 3, None)
    service.write_chunk(alice, up.id, 0, b"abc")
    service.complete_upload(alice, up.id)
    with pytest.raises(NotFound):  # a retried complete can't finalize again
        service.complete_upload(alice, up.id)

    lost = service.start_upload(alice, s.id, "b.bin", 3, None)
    service.write_chunk(alice, lost.id, 0, b"abc")
    store.delete_part(lost.id)  # e.g. the server crashed mid-complete
    with pytest.raises(NotFound):
        service.complete_upload(alice, lost.id)


def test_empty_file(service, make_sample, alice):
    s = make_sample(files=())

    f = _upload(service, alice, s.id, data=b"", name="empty.txt")

    assert (f.size_bytes, f.sha256) == (0, hashlib.sha256(b"").hexdigest())


@pytest.mark.parametrize("filename", ["../../etc/passwd", "a/b.bin", "a\\b.bin", "", ".."])
def test_unsafe_filenames_are_rejected(service, make_sample, alice, filename):  # U4
    s = make_sample(files=())

    with pytest.raises(BadRequest):
        service.start_upload(alice, s.id, filename, 10, None)


def test_duplicate_names_conflict(service, make_sample, alice):
    s = make_sample(files=("a.bin",))
    service.start_upload(alice, s.id, "b.bin", 10, None)

    with pytest.raises(Conflict) as existing:
        service.start_upload(alice, s.id, "a.bin", 10, None)
    with pytest.raises(Conflict) as in_progress:
        service.start_upload(alice, s.id, "b.bin", 10, None)
    assert (existing.value.code, in_progress.value.code) == (
        "duplicate_filename",
        "upload_in_progress",
    )


def test_only_owner_can_upload_or_see_the_upload(
    service, make_sample, alice, bob, mallory, admin
):  # U5
    s = make_sample(files=())
    service.grant_access(alice, s.id, "bob")

    with pytest.raises(Forbidden):
        service.start_upload(bob, s.id, "x.bin", 10, None)
    with pytest.raises(Forbidden):
        service.start_upload(admin, s.id, "x.bin", 10, None)
    with pytest.raises(NotFound):
        service.start_upload(mallory, s.id, "x.bin", 10, None)

    up = service.start_upload(alice, s.id, "x.bin", 10, None)
    with pytest.raises(NotFound):  # nobody else can see, continue or abort it
        service.write_chunk(bob, up.id, 0, b"x" * 10)


def test_abort_and_expiry_clean_up_partial_bytes(service, make_sample, alice, store, clock):
    s = make_sample(files=())
    aborted = service.start_upload(alice, s.id, "a.bin", 300, None)
    service.write_chunk(alice, aborted.id, 0, b"x" * 100)
    abandoned = service.start_upload(alice, s.id, "b.bin", 300, None)

    service.abort_upload(alice, aborted.id)
    clock.advance(25 * 3600)
    assert service.purge_expired_uploads() == 1

    assert list(store.root.iterdir()) == []
    with pytest.raises(NotFound):
        service.get_upload(alice, abandoned.id)


def test_deleting_sample_removes_files_and_in_progress_uploads(service, make_sample, alice, store):
    s = make_sample(files=())
    done = _upload(service, alice, s.id)
    pending = service.start_upload(alice, s.id, "partial.bin", 300, None)
    service.write_chunk(alice, pending.id, 0, b"x" * 100)

    service.delete_sample(alice, s.id)

    assert list(store.root.iterdir()) == []
    assert service.repo.get_file(done.id) is None


# --- API ------------------------------------------------------------------------------------

API_CHUNK = 64 * 1024


@pytest.fixture
def alice_client(login_as, api_users):
    return login_as("alice")


@pytest.fixture
def sample_id(alice_client):
    return alice_client.post("/api/samples", json={"name": "s"}).json()["id"]


def _api_upload(client, sample_id, data, name="video.mp4", content_type="video/mp4"):
    r = client.post(
        f"/api/samples/{sample_id}/uploads",
        json={"filename": name, "size_bytes": len(data), "content_type": content_type},
    )
    assert r.status_code == 201, r.text
    up = r.json()
    for offset in range(0, len(data), up["chunk_bytes"]):
        r = client.put(
            f"/api/uploads/{up['id']}",
            params={"offset": offset},
            content=data[offset : offset + up["chunk_bytes"]],
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
    return client.post(f"/api/uploads/{up['id']}/complete")


def test_chunked_upload_qc_download_roundtrip(alice_client, sample_id, pipeline):  # U6
    data = bytes(range(256)) * 1000  # 256 KB: four 64 KB chunks
    r = _api_upload(alice_client, sample_id, data, name="<script>.html", content_type="text/html")
    assert r.status_code == 201
    file_id = r.json()["id"]
    assert r.json()["sha256"] == hashlib.sha256(data).hexdigest()
    assert r.json()["content_type"] == "text/html"

    r = alice_client.post(f"/api/files/{file_id}/download-url")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "qc_pending")

    alice_client.put(
        f"/api/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline
    )
    url = alice_client.post(f"/api/files/{file_id}/download-url").json()["url"]
    assert urlparse(url).path == f"/api/downloads/{file_id}"

    r = alice_client.get(url)
    assert r.content == data
    # Served as an opaque attachment, never rendered: uploaded HTML can't run script here.
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"

    # Large downloads can resume: HTTP Range requests are honoured.
    part = alice_client.get(url, headers={"Range": "bytes=1000-1999"})
    assert (part.status_code, part.content) == (206, data[1000:2000])


def test_over_cap_and_oversized_chunk_are_413(alice_client, sample_id):  # U7
    r = alice_client.post(
        f"/api/samples/{sample_id}/uploads",
        json={"filename": "huge.mov", "size_bytes": 1024 * 1024 + 1},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (413, "file_too_large")

    up = alice_client.post(
        f"/api/samples/{sample_id}/uploads", json={"filename": "a.bin", "size_bytes": 200_000}
    ).json()
    r = alice_client.put(
        f"/api/uploads/{up['id']}", params={"offset": 0}, content=b"x" * (API_CHUNK + 1)
    )
    assert (r.status_code, r.json()["error"]["code"]) == (413, "bad_chunk")


def test_resume_over_http(alice_client, sample_id):
    up = alice_client.post(
        f"/api/samples/{sample_id}/uploads", json={"filename": "a.bin", "size_bytes": 100}
    ).json()
    alice_client.put(f"/api/uploads/{up['id']}", params={"offset": 0}, content=b"x" * 60)

    status = alice_client.get(f"/api/uploads/{up['id']}").json()
    assert status["received_bytes"] == 60
    r = alice_client.put(f"/api/uploads/{up['id']}", params={"offset": 0}, content=b"x" * 40)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "offset_mismatch")
    alice_client.put(f"/api/uploads/{up['id']}", params={"offset": 60}, content=b"x" * 40)
    assert alice_client.post(f"/api/uploads/{up['id']}/complete").status_code == 201


def test_stranger_cannot_upload(login_as, api_users, sample_id):
    mallory = login_as("mallory")

    r = mallory.post(
        f"/api/samples/{sample_id}/uploads", json={"filename": "x.bin", "size_bytes": 1}
    )
    assert r.status_code == 404


def test_metadata_only_file_downloads_placeholder(alice_client, pipeline):
    s = alice_client.post(
        "/api/samples", json={"name": "s", "files": [{"filename": "remote.bam"}]}
    ).json()
    file_id = s["files"][0]["id"]
    assert s["files"][0]["has_content"] is False
    alice_client.put(
        f"/api/internal/files/{file_id}/qc-status", json={"status": "passed"}, headers=pipeline
    )

    r = alice_client.get(alice_client.post(f"/api/files/{file_id}/download-url").json()["url"])

    assert r.status_code == 200
    assert r.text.startswith("placeholder")
