import secrets
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.clock import FrozenClock
from app.config import Settings
from app.domain import users as users_module
from app.domain.audit import Auditor
from app.domain.models import Caller, QcStatus, Role, User
from app.domain.service import AccessService
from app.domain.users import UserService, hash_password
from app.main import create_app
from app.persistence.repository import SqliteRepository, connect, init_schema
from app.signing import UrlSigner
from app.storage import LocalDiskStore
from tests.constants import ADMIN, BASE_URL, CHUNK_BYTES, CSRF, MAX_UPLOAD_BYTES, PASSWORD


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    # Production scrypt cost is deliberately slow; tests use a cheap cost. The cost is stored
    # inside each hash, so verification still exercises the real code path.
    monkeypatch.setattr(users_module, "_SCRYPT_N", 2**8)


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch):
    # Tests must not depend on the host's free disk space; test_uploads overrides this.
    monkeypatch.setattr(LocalDiskStore, "free_bytes", lambda self: 1024**4)


@pytest.fixture
def clock():
    return FrozenClock(datetime(2026, 10, 29, 14, 0, tzinfo=UTC))


@pytest.fixture
def signing_secret():
    return secrets.token_urlsafe(32)


@pytest.fixture
def signer(signing_secret):
    return UrlSigner(signing_secret, BASE_URL)


# --- domain fixtures ------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "test.db"))
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def repo(conn):
    return SqliteRepository(conn)


@pytest.fixture
def audit(repo, clock):
    return Auditor(repo, clock)


@pytest.fixture
def store(tmp_path):
    return LocalDiskStore(str(tmp_path / "storage"))


@pytest.fixture
def service(repo, signer, clock, audit, store):
    return AccessService(
        repo,
        signer,
        clock,
        audit,
        store,
        url_ttl_seconds=300,
        max_upload_bytes=MAX_UPLOAD_BYTES,
        chunk_bytes=CHUNK_BYTES,
    )


@pytest.fixture
def user_service(repo, clock, audit):
    return UserService(repo, clock, audit, session_ttl_hours=8)


@pytest.fixture
def make_user(repo, clock):
    password_hash = hash_password(PASSWORD)

    def _make(username: str, role: Role = Role.USER) -> Caller:
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            role=role,
            is_active=True,
            created_at=clock.now(),
            password_hash=password_hash,
        )
        assert repo.add_user(user)
        return Caller(id=user.id, username=username, role=role)

    return _make


@pytest.fixture
def alice(make_user):
    return make_user("alice")


@pytest.fixture
def bob(make_user):
    return make_user("bob")


@pytest.fixture
def mallory(make_user):
    return make_user("mallory")


@pytest.fixture
def admin(make_user):
    return make_user("root", Role.ADMIN)


@pytest.fixture
def make_sample(service, alice):
    def _make(owner=None, files=("tumor.bam", "normal.bam"), name="sample-1"):
        return service.register_sample(owner or alice, name, list(files))

    return _make


@pytest.fixture
def pass_qc(service):
    def _pass(file_id):
        service.update_qc(file_id, QcStatus.PASSED)

    return _pass


# --- API fixtures ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path, signing_secret):
    return Settings(
        signing_secret=signing_secret,
        qc_callback_token=secrets.token_urlsafe(32),
        admin_username=ADMIN,
        admin_password=PASSWORD,
        db_path=str(tmp_path / "api.db"),
        storage_dir=str(tmp_path / "api-storage"),
        public_base_url=BASE_URL,
        cookie_secure=False,  # TestClient talks plain http
        max_upload_mb=1,
        upload_chunk_bytes=64 * 1024,
        frontend_dist=str(tmp_path / "no-frontend"),
    )


@pytest.fixture
def app(settings, clock):
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app):
    """Anonymous client (no session), sending the CSRF header."""
    return TestClient(app, headers=CSRF)


@pytest.fixture
def login_as(app):
    """Returns a signed-in client. Each user gets its own client, so cookies don't mix."""

    def _login(username: str, password: str = PASSWORD) -> TestClient:
        c = TestClient(app, headers=CSRF)
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return c

    return _login


@pytest.fixture
def admin_client(login_as):
    return login_as(ADMIN)


@pytest.fixture
def api_users(admin_client):
    """Creates alice, bob and mallory through the admin API; returns {username: id}."""
    ids = {}
    for name in ("alice", "bob", "mallory"):
        r = admin_client.post("/api/admin/users", json={"username": name, "password": PASSWORD})
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    return ids


@pytest.fixture
def pipeline(settings):
    return {"X-Pipeline-Token": settings.qc_callback_token}
