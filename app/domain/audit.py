import json
import logging

from app.clock import Clock
from app.persistence.repository import SqliteRepository

audit_log = logging.getLogger("audit")


class Auditor:
    """Writes each security-relevant event to stdout (JSON) and to the audit_events table.

    Only IDs and codes are recorded: never filenames, sample names, passwords, tokens,
    signatures or URLs.
    """

    def __init__(self, repo: SqliteRepository, clock: Clock):
        self.repo = repo
        self.clock = clock

    def __call__(
        self,
        event: str,
        *,
        actor_id: str | None = None,
        sample_id: str | None = None,
        file_id: str | None = None,
        level: int = logging.INFO,
        **details: object,
    ) -> None:
        now = self.clock.now()
        record = {
            "ts": now.isoformat(),
            "event": event,
            "actor_id": actor_id,
            "sample_id": sample_id,
            "file_id": file_id,
            **details,
        }
        audit_log.log(level, json.dumps({k: v for k, v in record.items() if v is not None}))
        self.repo.add_audit_event(now, event, actor_id, sample_id, file_id, details)
