from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class QcStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class File:
    id: str
    sample_id: str
    filename: str
    qc_status: QcStatus
    qc_updated_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class Sample:
    id: str
    owner_id: str
    name: str
    created_at: datetime
    files: list[File] = field(default_factory=list)


@dataclass(frozen=True)
class Grant:
    sample_id: str
    user_id: str
    granted_by: str
    granted_at: datetime


@dataclass(frozen=True)
class SignedUrl:
    url: str
    expires_at: datetime
