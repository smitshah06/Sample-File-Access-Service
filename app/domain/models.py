from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class QcStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True)
class Caller:
    """The authenticated user making a request. Only the API layer creates these."""

    id: str
    username: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: Role
    is_active: bool
    created_at: datetime
    password_hash: str = field(default="", repr=False)
    locked_until: datetime | None = None


@dataclass(frozen=True)
class File:
    id: str
    sample_id: str
    filename: str
    qc_status: QcStatus
    qc_updated_at: datetime | None
    created_at: datetime
    size_bytes: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    storage_key: str | None = None

    @property
    def has_content(self) -> bool:
        return self.storage_key is not None


@dataclass(frozen=True)
class Upload:
    """A resumable upload in progress: the client sends chunks, then completes it."""

    id: str
    sample_id: str
    owner_id: str
    filename: str
    content_type: str
    size_bytes: int
    received_bytes: int
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class Sample:
    id: str
    owner_id: str
    name: str
    created_at: datetime
    files: list[File] = field(default_factory=list)
    owner_username: str | None = None


@dataclass(frozen=True)
class SampleSummary:
    """A row in a sample list, with the viewer's relationship to it."""

    id: str
    name: str
    owner_id: str
    owner_username: str
    created_at: datetime
    file_count: int
    access: str  # "owner" | "grantee" | "none" (admin viewing a sample it has no grant on)


@dataclass(frozen=True)
class Grant:
    sample_id: str
    user_id: str
    granted_by: str
    granted_at: datetime
    username: str | None = None


@dataclass(frozen=True)
class SignedUrl:
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    id: int
    ts: datetime
    event: str
    actor_id: str | None
    sample_id: str | None
    file_id: str | None
    details: dict
