"""SQLite persistence. Stores and loads data; never decides who is allowed to do what."""

import sqlite3
from datetime import datetime
from pathlib import Path

from app.domain.models import File, Grant, QcStatus, Sample

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str) -> sqlite3.Connection:
    # One connection per request. FastAPI may open and close it on different threadpool
    # threads, but it is never used concurrently, so the same-thread check is disabled.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _file(row: sqlite3.Row) -> File:
    return File(
        id=row["id"],
        sample_id=row["sample_id"],
        filename=row["filename"],
        qc_status=QcStatus(row["qc_status"]),
        qc_updated_at=_dt(row["qc_updated_at"]),
        created_at=_dt(row["created_at"]),
    )


def _sample(row: sqlite3.Row, files: list[File] | None = None) -> Sample:
    return Sample(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        created_at=_dt(row["created_at"]),
        files=files or [],
    )


class SqliteRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def ping(self) -> None:
        self.conn.execute("SELECT 1").fetchone()

    def add_sample(self, sample: Sample) -> None:
        """Insert the sample and all its files in one transaction: all or nothing."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO samples (id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
                (sample.id, sample.owner_id, sample.name, sample.created_at.isoformat()),
            )
            self.conn.executemany(
                "INSERT INTO files (id, sample_id, filename, qc_status, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (f.id, f.sample_id, f.filename, f.qc_status.value, f.created_at.isoformat())
                    for f in sample.files
                ],
            )

    def get_sample(self, sample_id: str, with_files: bool = False) -> Sample | None:
        row = self.conn.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
        if row is None:
            return None
        files = None
        if with_files:
            rows = self.conn.execute(
                "SELECT * FROM files WHERE sample_id = ? ORDER BY rowid",
                (sample_id,),
            ).fetchall()
            files = [_file(r) for r in rows]
        return _sample(row, files)

    def get_file(self, file_id: str) -> File | None:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return _file(row) if row is not None else None

    def get_file_with_sample(self, file_id: str) -> tuple[File, Sample] | None:
        row = self.conn.execute(
            "SELECT f.*, s.owner_id, s.name, s.created_at AS sample_created_at"
            " FROM files f JOIN samples s ON s.id = f.sample_id WHERE f.id = ?",
            (file_id,),
        ).fetchone()
        if row is None:
            return None
        sample = Sample(
            id=row["sample_id"],
            owner_id=row["owner_id"],
            name=row["name"],
            created_at=_dt(row["sample_created_at"]),
        )
        return _file(row), sample

    def has_grant(self, sample_id: str, user_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM access_grants WHERE sample_id = ? AND user_id = ?",
            (sample_id, user_id),
        ).fetchone()
        return row is not None

    def get_grant(self, sample_id: str, user_id: str) -> Grant | None:
        row = self.conn.execute(
            "SELECT * FROM access_grants WHERE sample_id = ? AND user_id = ?",
            (sample_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return Grant(
            sample_id=row["sample_id"],
            user_id=row["user_id"],
            granted_by=row["granted_by"],
            granted_at=_dt(row["granted_at"]),
        )

    def add_grant(self, grant: Grant) -> bool:
        """Returns True if a new grant row was created, False if it already existed."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO access_grants (sample_id, user_id, granted_by, granted_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (sample_id, user_id) DO NOTHING",
                (grant.sample_id, grant.user_id, grant.granted_by, grant.granted_at.isoformat()),
            )
        return cur.rowcount == 1

    def set_qc_status_if_pending(self, file_id: str, status: QcStatus, now: datetime) -> bool:
        """Atomic compare-and-set: only a pending file can move. True if this call moved it."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE files SET qc_status = ?, qc_updated_at = ?"
                " WHERE id = ? AND qc_status = 'pending'",
                (status.value, now.isoformat(), file_id),
            )
        return cur.rowcount == 1
