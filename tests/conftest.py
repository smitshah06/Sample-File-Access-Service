import secrets
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.clock import FrozenClock
from app.config import Settings
from app.domain.models import QcStatus
from app.domain.service import AccessService
from app.main import create_app
from app.persistence.repository import SqliteRepository, connect, init_schema
from app.signing import UrlSigner

BASE_URL = "http://testserver"


@pytest.fixture
def clock():
    return FrozenClock(datetime(2026, 10, 29, 14, 0, tzinfo=UTC))


@pytest.fixture
def signing_secret():
    return secrets.token_urlsafe(32)


@pytest.fixture
def signer(signing_secret):
    return UrlSigner(signing_secret, BASE_URL)


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "test.db"))
    init_schema(c)
    yield c
    c.close()


@pytest.fixture
def service(conn, signer, clock):
    return AccessService(SqliteRepository(conn), signer, clock, url_ttl_seconds=300)


@pytest.fixture
def make_sample(service):
    def _make(owner="alice", files=("tumor.bam", "normal.bam"), name="sample-1"):
        return service.register_sample(owner, name, list(files))

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
        db_path=str(tmp_path / "api.db"),
        public_base_url=BASE_URL,
    )


@pytest.fixture
def client(settings, clock):
    with TestClient(create_app(settings, clock=clock)) as c:
        yield c
