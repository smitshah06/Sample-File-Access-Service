"""All business rules live here. No HTTP, no SQL: tests call this class directly."""

import json
import logging
import uuid
from datetime import timedelta

from app.clock import Clock
from app.domain.errors import (
    BadRequest,
    Forbidden,
    InvalidLink,
    InvalidTransition,
    NotFound,
    QcNotPassed,
)
from app.domain.models import File, Grant, QcStatus, Sample, SignedUrl
from app.persistence.repository import SqliteRepository
from app.signing import UrlSigner

audit_log = logging.getLogger("audit")


class AccessService:
    def __init__(
        self,
        repo: SqliteRepository,
        signer: UrlSigner,
        clock: Clock,
        url_ttl_seconds: int = 300,
    ):
        self.repo = repo
        self.signer = signer
        self.clock = clock
        self.url_ttl = timedelta(seconds=url_ttl_seconds)

    # --- samples and grants ---------------------------------------------------------------

    def register_sample(self, caller_id: str, name: str, filenames: list[str]) -> Sample:
        if len(set(filenames)) != len(filenames):
            raise BadRequest("filenames must be unique within a sample", code="duplicate_filename")
        now = self.clock.now()
        sample_id = str(uuid.uuid4())
        files = [
            File(
                id=str(uuid.uuid4()),
                sample_id=sample_id,
                filename=filename,
                qc_status=QcStatus.PENDING,
                qc_updated_at=None,
                created_at=now,
            )
            for filename in filenames
        ]
        sample = Sample(id=sample_id, owner_id=caller_id, name=name, created_at=now, files=files)
        self.repo.add_sample(sample)
        self._audit("sample_registered", actor=caller_id, sample_id=sample_id, files=len(files))
        return sample

    def get_sample(self, caller_id: str, sample_id: str) -> Sample:
        sample = self.repo.get_sample(sample_id, with_files=True)
        if sample is None or not self._can_access(caller_id, sample):
            raise NotFound("sample")
        return sample

    def grant_access(self, caller_id: str, sample_id: str, user_id: str) -> tuple[Grant, bool]:
        """Returns (grant, created). Granting an existing grant again is a no-op."""
        sample = self.repo.get_sample(sample_id)
        if sample is None:
            raise NotFound("sample")
        if caller_id != sample.owner_id:
            # A grantee already knows the sample exists, so 403 reveals nothing new to them.
            # Anyone else gets the same 404 as for a sample that doesn't exist.
            if self.repo.has_grant(sample_id, caller_id):
                raise Forbidden("only the sample owner can grant access")
            raise NotFound("sample")
        if user_id == sample.owner_id:
            raise BadRequest("the owner already has access", code="cannot_grant_owner")

        grant = Grant(
            sample_id=sample_id, user_id=user_id, granted_by=caller_id, granted_at=self.clock.now()
        )
        created = self.repo.add_grant(grant)
        if created:
            self._audit("access_granted", actor=caller_id, sample_id=sample_id, grantee=user_id)
        else:
            grant = self.repo.get_grant(sample_id, user_id)
        return grant, created

    # --- QC ---------------------------------------------------------------------------------

    def update_qc(self, file_id: str, status: QcStatus) -> tuple[File, bool]:
        """pending -> passed|failed, exactly once. Returns (file, changed).

        Repeating the current terminal status is an idempotent no-op (pipelines retry).
        Asking for the other terminal status is a conflict.
        """
        if status is QcStatus.PENDING:
            raise BadRequest("QC status can only be set to passed or failed", code="invalid_status")

        if self.repo.set_qc_status_if_pending(file_id, status, self.clock.now()):
            self._audit("qc_status_changed", file_id=file_id, qc_status=status.value)
            return self.repo.get_file(file_id), True

        file = self.repo.get_file(file_id)
        if file is None:
            raise NotFound("file")
        if file.qc_status is status:
            return file, False
        self._audit(
            "qc_transition_rejected",
            level=logging.WARNING,
            file_id=file_id,
            current=file.qc_status.value,
            requested=status.value,
        )
        raise InvalidTransition(file.qc_status, status)

    # --- downloads --------------------------------------------------------------------------

    def request_download(self, caller_id: str, file_id: str) -> SignedUrl:
        self._authorize_download(caller_id, file_id)
        expires_at = self.clock.now() + self.url_ttl
        url = self.signer.sign(file_id, caller_id, int(expires_at.timestamp()))
        self._audit("download_url_issued", actor=caller_id, file_id=file_id)
        return SignedUrl(url=url, expires_at=expires_at)

    def verify_download(self, file_id: str, user_id: str, exp: int, sig: str) -> File:
        """Called when a signed URL is used. Checks the signature, then re-checks the rule."""
        now = int(self.clock.now().timestamp())
        if not self.signer.verify(file_id, user_id, exp, sig, now=now):
            self._audit("download_link_rejected", actor=user_id, file_id=file_id)
            raise InvalidLink()
        file = self._authorize_download(user_id, file_id)
        self._audit("download_served", actor=user_id, file_id=file_id)
        return file

    def _authorize_download(self, caller_id: str, file_id: str) -> File:
        """The core rule: (owner or grantee) AND qc_status == passed.

        Access is checked before QC so that outsiders can't learn a file's QC status,
        and "not yours" is the same NotFound as "doesn't exist".
        """
        found = self.repo.get_file_with_sample(file_id)
        if found is None or not self._can_access(caller_id, found[1]):
            self._audit("download_denied", actor=caller_id, file_id=file_id, reason="not_found")
            raise NotFound("file")
        file = found[0]
        if file.qc_status is not QcStatus.PASSED:
            self._audit(
                "download_denied", actor=caller_id, file_id=file_id, reason=f"qc_{file.qc_status}"
            )
            raise QcNotPassed(file.qc_status)
        return file

    def _can_access(self, caller_id: str, sample: Sample) -> bool:
        return caller_id == sample.owner_id or self.repo.has_grant(sample.id, caller_id)

    # --- audit ------------------------------------------------------------------------------

    def _audit(self, event: str, level: int = logging.INFO, **fields: object) -> None:
        # IDs only: never filenames, sample names, signatures, tokens or full URLs.
        record = {"ts": self.clock.now().isoformat(), "event": event, **fields}
        audit_log.log(level, json.dumps(record))
