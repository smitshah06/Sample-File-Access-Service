from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

USER_ID_PATTERN = r"^[A-Za-z0-9._@-]{1,128}$"
UserId = Annotated[str, Field(pattern=USER_ID_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests -------------------------------------------------------------------------------


class FileIn(_Strict):
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def _safe_filename(cls, v: str) -> str:
        if "/" in v or "\\" in v or any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("filename must not contain path separators or control characters")
        return v


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


class GrantCreate(_Strict):
    user_id: UserId


class QcUpdate(_Strict):
    status: Literal["passed", "failed"]


# --- responses ------------------------------------------------------------------------------


class FileOut(BaseModel):
    id: str
    filename: str
    qc_status: str
    qc_updated_at: datetime | None


class SampleOut(BaseModel):
    id: str
    owner_id: str
    name: str
    created_at: datetime
    files: list[FileOut]


class GrantOut(BaseModel):
    sample_id: str
    user_id: str
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


class DownloadOut(BaseModel):
    file_id: str
    note: str
