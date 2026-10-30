from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.domain.errors import BadRequest
from app.domain.service import validate_filename
from app.domain.users import USERNAME_PATTERN

Username = Annotated[str, Field(pattern=USERNAME_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests -------------------------------------------------------------------------------


class LoginIn(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(_Strict):
    username: Username
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "user"] = "user"


class UserUpdate(_Strict):
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


def _filename(v: str) -> str:
    """Same rules as the domain (one source of truth), reported as a 422."""
    try:
        return validate_filename(v)
    except BadRequest as exc:
        raise ValueError(exc.message) from exc


Filename = Annotated[str, AfterValidator(_filename)]


class FileIn(_Strict):
    filename: Filename


class SampleCreate(_Strict):
    name: str = Field(min_length=1, max_length=200)
    files: list[FileIn] = Field(default_factory=list, max_length=1000)

    @field_validator("files")
    @classmethod
    def _unique_filenames(cls, v: list[FileIn]) -> list[FileIn]:
        names = [f.filename for f in v]
        if len(set(names)) != len(names):
            raise ValueError("filenames must be unique within a sample")
        return v


class UploadCreate(_Strict):
    filename: Filename
    size_bytes: int = Field(ge=0)
    content_type: str | None = Field(default=None, max_length=255)


class GrantCreate(_Strict):
    username: Username


class QcUpdate(_Strict):
    status: Literal["passed", "failed"]


# --- responses ------------------------------------------------------------------------------


class MeOut(BaseModel):
    id: str
    username: str
    role: str


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime


class FileOut(BaseModel):
    id: str
    filename: str
    qc_status: str
    qc_updated_at: datetime | None
    created_at: datetime
    has_content: bool
    size_bytes: int | None
    sha256: str | None
    content_type: str | None


class UploadOut(BaseModel):
    id: str
    sample_id: str
    filename: str
    size_bytes: int
    received_bytes: int
    chunk_bytes: int
    expires_at: datetime


class SampleOut(BaseModel):
    id: str
    owner_id: str
    owner_username: str | None
    name: str
    created_at: datetime
    access: Literal["owner", "grantee", "none"]
    files: list[FileOut]


class SampleSummaryOut(BaseModel):
    id: str
    name: str
    owner_id: str
    owner_username: str
    created_at: datetime
    file_count: int
    access: Literal["owner", "grantee", "none"]


class GrantOut(BaseModel):
    sample_id: str
    user_id: str
    username: str | None
    granted_by: str
    granted_at: datetime


class QcUpdateOut(BaseModel):
    file_id: str
    qc_status: str
    changed: bool
    qc_updated_at: datetime | None


class DownloadUrlOut(BaseModel):
    url: str
    expires_at: datetime


class AuditEventOut(BaseModel):
    id: int
    ts: datetime
    event: str
    actor_id: str | None
    sample_id: str | None
    file_id: str | None
    details: dict
