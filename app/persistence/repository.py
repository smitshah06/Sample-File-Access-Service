"""SQLite persistence. Stores and loads data; never decides who is allowed to do what."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.domain.models import (
    AuditEvent,
    File,
    Grant,
    QcStatus,
    Role,
    Sample,
    SampleSummary,
    Upload,
    User,
)

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


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _file(row: sqlite3.Row) -> File:
    return File(
        id=row["id"],
        sample_id=row["sample_id"],
        filename=row["filename"],
        qc_status=QcStatus(row["qc_status"]),
        qc_updated_at=_dt(row["qc_updated_at"]),
        created_at=_dt(row["created_at"]),
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        content_type=row["content_type"],
        storage_key=row["storage_key"],
    )


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=Role(row["role"]),
        is_active=bool(row["is_active"]),
        created_at=_dt(row["created_at"]),
        password_hash=row["password_hash"],
        locked_until=_dt(row["locked_until"]),
    )


class SqliteRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def ping(self) -> None:
        self.conn.execute("SELECT 1").fetchone()

    # --- users ------------------------------------------------------------------------------

    def add_user(self, user: User) -> bool:
        """False if the username is already taken (case-insensitive)."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, is_active, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user.id,
                        user.username,
                        user.password_hash,
                        user.role.value,
                        int(user.is_active),
                        _iso(user.created_at),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get_user(self, user_id: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row is not None else None

    def get_user_by_username(self, username: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _user(row) if row is not None else None

    def list_users(self) -> list[User]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(r) for r in rows]

    def any_admin(self) -> bool:
        return self.conn.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone() is not None

    def update_user(self, user_id: str, role: Role | None, is_active: bool | None) -> None:
        with self.conn:
            if role is not None:
                self.conn.execute("UPDATE users SET role = ? WHERE id = ?", (role.value, user_id))
            if is_active is not None:
                self.conn.execute(
                    "UPDATE users SET is_active = ? WHERE id = ?", (int(is_active), user_id)
                )

    def record_login_failure(
        self,
        user_id: str,
        now: datetime,
        window_start: datetime,
        lock_after: int,
        locked_until: datetime,
    ) -> bool:
        """Count this failure (restarting the count if the previous one is older than
        `window_start`); lock the account when it reaches `lock_after`.
        Returns True if this failure locked the account."""
        with self.conn:
            self.conn.execute(
                "UPDATE users SET failed_logins = CASE"
                " WHEN last_failed_at IS NULL OR last_failed_at < :window THEN 1"
                " ELSE failed_logins + 1 END, last_failed_at = :now WHERE id = :id",
                {"window": _iso(window_start), "now": _iso(now), "id": user_id},
            )
            cur = self.conn.execute(
                "UPDATE users SET failed_logins = 0, locked_until = ?"
                " WHERE id = ? AND failed_logins >= ?",
                (_iso(locked_until), user_id, lock_after),
            )
        return cur.rowcount == 1

    def reset_login_failures(self, user_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE users SET failed_logins = 0, last_failed_at = NULL, locked_until = NULL"
                " WHERE id = ?",
                (user_id,),
            )

    # --- sessions ---------------------------------------------------------------------------

    def add_session(self, token_hash: str, user_id: str, now: datetime, expires: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (token_hash, user_id, _iso(now), _iso(expires)),
            )

    def get_session_user(self, token_hash: str, now: datetime) -> User | None:
        """The active user behind an unexpired session, or None."""
        row = self.conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1",
            (token_hash, _iso(now)),
        ).fetchone()
        return _user(row) if row is not None else None

    def delete_session(self, token_hash: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_expired_sessions(self, now: datetime) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(now),))

    def delete_user_sessions(self, user_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # --- samples and files ------------------------------------------------------------------

    def add_sample(self, sample: Sample) -> None:
        """Insert the sample and all its files in one transaction: all or nothing."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO samples (id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
                (sample.id, sample.owner_id, sample.name, _iso(sample.created_at)),
            )
            for f in sample.files:
                self._insert_file(f)

    def _insert_file(self, f: File) -> None:
        self.conn.execute(
            "INSERT INTO files (id, sample_id, filename, qc_status, created_at,"
            " size_bytes, sha256, content_type, storage_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f.id,
                f.sample_id,
                f.filename,
                f.qc_status.value,
                _iso(f.created_at),
                f.size_bytes,
                f.sha256,
                f.content_type,
                f.storage_key,
            ),
        )

    def add_file(self, f: File) -> bool:
        """False if the sample already has a file with this name."""
        try:
            with self.conn:
                self._insert_file(f)
        except sqlite3.IntegrityError:
            return False
        return True

    def filename_exists(self, sample_id: str, filename: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM files WHERE sample_id = ? AND filename = ?", (sample_id, filename)
        ).fetchone()
        return row is not None

    def get_sample(self, sample_id: str, with_files: bool = False) -> Sample | None:
        row = self.conn.execute(
            "SELECT s.*, u.username AS owner_username"
            " FROM samples s JOIN users u ON u.id = s.owner_id WHERE s.id = ?",
            (sample_id,),
        ).fetchone()
        if row is None:
            return None
        files = []
        if with_files:
            rows = self.conn.execute(
                "SELECT * FROM files WHERE sample_id = ? ORDER BY rowid", (sample_id,)
            ).fetchall()
            files = [_file(r) for r in rows]
        return Sample(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            created_at=_dt(row["created_at"]),
            files=files,
            owner_username=row["owner_username"],
        )

    def list_samples(self, viewer_id: str, include_all: bool) -> list[SampleSummary]:
        """Samples the viewer owns or was granted; every sample if include_all (admin)."""
        rows = self.conn.execute(
            "SELECT s.id, s.name, s.owner_id, s.created_at, u.username AS owner_username,"
            " (SELECT COUNT(*) FROM files f WHERE f.sample_id = s.id) AS file_count,"
            " CASE WHEN s.owner_id = :me THEN 'owner'"
            "      WHEN g.user_id IS NOT NULL THEN 'grantee' ELSE 'none' END AS access"
            " FROM samples s JOIN users u ON u.id = s.owner_id"
            " LEFT JOIN access_grants g ON g.sample_id = s.id AND g.user_id = :me"
            " WHERE :all OR s.owner_id = :me OR g.user_id IS NOT NULL"
            " ORDER BY s.created_at DESC, s.rowid DESC",
            {"me": viewer_id, "all": include_all},
        ).fetchall()
        return [
            SampleSummary(
                id=r["id"],
                name=r["name"],
                owner_id=r["owner_id"],
                owner_username=r["owner_username"],
                created_at=_dt(r["created_at"]),
                file_count=r["file_count"],
                access=r["access"],
            )
            for r in rows
        ]

    def delete_sample(self, sample_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM samples WHERE id = ?", (sample_id,))

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

    def delete_file(self, file_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def set_qc_status_if_pending(self, file_id: str, status: QcStatus, now: datetime) -> bool:
        """Atomic compare-and-set: only a pending file can move. True if this call moved it."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE files SET qc_status = ?, qc_updated_at = ?"
                " WHERE id = ? AND qc_status = 'pending'",
                (status.value, _iso(now), file_id),
            )
        return cur.rowcount == 1

    # --- resumable uploads ------------------------------------------------------------------

    @staticmethod
    def _upload(row: sqlite3.Row) -> Upload:
        return Upload(
            id=row["id"],
            sample_id=row["sample_id"],
            owner_id=row["owner_id"],
            filename=row["filename"],
            content_type=row["content_type"],
            size_bytes=row["size_bytes"],
            received_bytes=row["received_bytes"],
            created_at=_dt(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
        )

    def add_upload(self, up: Upload) -> bool:
        """False if an upload with this filename is already in progress for the sample."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO uploads (id, sample_id, owner_id, filename, content_type,"
                    " size_bytes, received_bytes, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        up.id,
                        up.sample_id,
                        up.owner_id,
                        up.filename,
                        up.content_type,
                        up.size_bytes,
                        up.received_bytes,
                        _iso(up.created_at),
                        _iso(up.expires_at),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get_upload(self, upload_id: str) -> Upload | None:
        row = self.conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        return self._upload(row) if row is not None else None

    def advance_upload(self, upload_id: str, expected: int, new: int) -> bool:
        """Compare-and-set on received_bytes, so two writers of the same chunk can't both
        move the offset."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE uploads SET received_bytes = ? WHERE id = ? AND received_bytes = ?",
                (new, upload_id, expected),
            )
        return cur.rowcount == 1

    def delete_upload(self, upload_id: str) -> bool:
        """True if this call removed the row (so exactly one caller can claim an upload)."""
        with self.conn:
            cur = self.conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        return cur.rowcount == 1

    def list_uploads_for_sample(self, sample_id: str) -> list[Upload]:
        rows = self.conn.execute(
            "SELECT * FROM uploads WHERE sample_id = ?", (sample_id,)
        ).fetchall()
        return [self._upload(r) for r in rows]

    def list_expired_uploads(self, now: datetime) -> list[Upload]:
        rows = self.conn.execute(
            "SELECT * FROM uploads WHERE expires_at <= ?", (_iso(now),)
        ).fetchall()
        return [self._upload(r) for r in rows]

    # --- grants -----------------------------------------------------------------------------

    def has_grant(self, sample_id: str, user_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM access_grants WHERE sample_id = ? AND user_id = ?",
            (sample_id, user_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _grant(row: sqlite3.Row) -> Grant:
        return Grant(
            sample_id=row["sample_id"],
            user_id=row["user_id"],
            granted_by=row["granted_by"],
            granted_at=_dt(row["granted_at"]),
            username=row["username"],
        )

    def get_grant(self, sample_id: str, user_id: str) -> Grant | None:
        row = self.conn.execute(
            "SELECT g.*, u.username FROM access_grants g JOIN users u ON u.id = g.user_id"
            " WHERE g.sample_id = ? AND g.user_id = ?",
            (sample_id, user_id),
        ).fetchone()
        return self._grant(row) if row is not None else None

    def list_grants(self, sample_id: str) -> list[Grant]:
        rows = self.conn.execute(
            "SELECT g.*, u.username FROM access_grants g JOIN users u ON u.id = g.user_id"
            " WHERE g.sample_id = ? ORDER BY g.granted_at, u.username",
            (sample_id,),
        ).fetchall()
        return [self._grant(r) for r in rows]

    def add_grant(self, grant: Grant) -> bool:
        """Returns True if a new grant row was created, False if it already existed."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO access_grants (sample_id, user_id, granted_by, granted_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (sample_id, user_id) DO NOTHING",
                (grant.sample_id, grant.user_id, grant.granted_by, _iso(grant.granted_at)),
            )
        return cur.rowcount == 1

    def delete_grant(self, sample_id: str, user_id: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM access_grants WHERE sample_id = ? AND user_id = ?",
                (sample_id, user_id),
            )
        return cur.rowcount == 1

    # --- audit ------------------------------------------------------------------------------

    def add_audit_event(
        self,
        ts: datetime,
        event: str,
        actor_id: str | None,
        sample_id: str | None,
        file_id: str | None,
        details: dict,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO audit_events (ts, event, actor_id, sample_id, file_id, details)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_iso(ts), event, actor_id, sample_id, file_id, json.dumps(details)),
            )

    def list_audit_events(self, limit: int, event: str | None = None) -> list[AuditEvent]:
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE :event IS NULL OR event = :event"
            " ORDER BY id DESC LIMIT :limit",
            {"event": event, "limit": limit},
        ).fetchall()
        return [
            AuditEvent(
                id=r["id"],
                ts=_dt(r["ts"]),
                event=r["event"],
                actor_id=r["actor_id"],
                sample_id=r["sample_id"],
                file_id=r["file_id"],
                details=json.loads(r["details"]),
            )
            for r in rows
        ]
