"""All sample, file and access rules live here. No HTTP, no SQL: tests call this class directly.

Who may do what:
- owner:   everything on their own sample (content + sharing)
- grantee: read the sample and download QC-passed files
- admin:   see every sample and manage its sharing, but NOT read or change content unless
           they are also owner or grantee (least privilege; granting themselves is audited)
"""

import logging
import re
import uuid
from datetime import timedelta

from app.clock import Clock
from app.domain.audit import Auditor
from app.domain.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    InsufficientStorage,
    InvalidLink,
    InvalidTransition,
    NotFound,
    PayloadTooLarge,
    QcNotPassed,
)
from app.domain.models import (
    AuditEvent,
    Caller,
    File,
    Grant,
    QcStatus,
    Sample,
    SampleSummary,
    SignedUrl,
    Upload,
)
from app.persistence.repository import SqliteRepository
from app.signing import UrlSigner
from app.storage import FileStore

GIB = 1024**3
DEFAULT_MAX_UPLOAD_BYTES = 10 * GIB
DEFAULT_CHUNK_BYTES = 16 * 1024 * 1024
UPLOAD_SESSION_TTL = timedelta(hours=24)  # abandoned uploads are purged after this
DEFAULT_FREE_SPACE_RESERVE = 1 * GIB  # never let uploads fill the disk completely

_BAD_FILENAME = re.compile(r"[/\\\x00-\x1f\x7f]")


def validate_filename(filename: str) -> str:
    if not 1 <= len(filename) <= 255 or _BAD_FILENAME.search(filename) or filename in {".", ".."}:
        raise BadRequest(
            "filename must be 1-255 characters with no path separators or control characters",
            code="bad_filename",
        )
    return filename


class AccessService:
    def __init__(
        self,
        repo: SqliteRepository,
        signer: UrlSigner,
        clock: Clock,
        audit: Auditor,
        store: FileStore,
        url_ttl_seconds: int = 300,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        free_space_reserve: int = DEFAULT_FREE_SPACE_RESERVE,
    ):
        self.repo = repo
        self.signer = signer
        self.clock = clock
        self.audit = audit
        self.store = store
        self.url_ttl = timedelta(seconds=url_ttl_seconds)
        self.max_upload_bytes = max_upload_bytes
        self.chunk_bytes = chunk_bytes
        self.free_space_reserve = free_space_reserve

    # --- samples ----------------------------------------------------------------------------

    def register_sample(self, caller: Caller, name: str, filenames: list[str]) -> Sample:
        """Create a sample owned by the caller. `filenames` registers metadata-only files
        (bytes stored elsewhere, e.g. by a pipeline); content is added with start_upload()."""
        for filename in filenames:
            validate_filename(filename)
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
        sample = Sample(
            id=sample_id,
            owner_id=caller.id,
            name=name,
            created_at=now,
            files=files,
            owner_username=caller.username,
        )
        self.repo.add_sample(sample)
        self.audit("sample_registered", actor_id=caller.id, sample_id=sample_id, files=len(files))
        return sample

    def list_samples(self, caller: Caller) -> list[SampleSummary]:
        """Samples the caller owns or was granted."""
        return self.repo.list_samples(caller.id, include_all=False)

    def list_all_samples(self, caller: Caller) -> list[SampleSummary]:
        self._require_admin(caller)
        return self.repo.list_samples(caller.id, include_all=True)

    def list_audit_events(
        self, caller: Caller, limit: int = 100, event: str | None = None
    ) -> list[AuditEvent]:
        self._require_admin(caller)
        return self.repo.list_audit_events(limit, event)

    def get_sample(self, caller: Caller, sample_id: str) -> tuple[Sample, str]:
        """Returns (sample, access) where access is owner | grantee | none (admin)."""
        sample = self.repo.get_sample(sample_id, with_files=True)
        if sample is None:
            raise NotFound("sample")
        access = self._access(caller, sample)
        if access == "none" and not caller.is_admin:
            raise NotFound("sample")
        return sample, access

    def delete_sample(self, caller: Caller, sample_id: str) -> None:
        sample = self._sample_for_owner(caller, sample_id)
        in_progress = self.repo.list_uploads_for_sample(sample.id)
        self.repo.delete_sample(sample.id)  # cascades to files, grants and uploads
        for f in sample.files:
            if f.storage_key:
                self.store.delete(f.storage_key)
        for up in in_progress:
            self.store.delete_part(up.id)
        self.audit(
            "sample_deleted", actor_id=caller.id, sample_id=sample.id, files=len(sample.files)
        )

    # --- resumable uploads ------------------------------------------------------------------
    #
    # Any file type is accepted: the bytes are never interpreted or rendered by this service
    # (downloads are always opaque attachments). Large files are sent as a series of bounded
    # chunks into an upload session, so:
    #   - memory per request is bounded by the chunk size, not the file size
    #   - the size cap is checked before a single byte is stored
    #   - an interrupted upload resumes from the last stored byte
    #   - each byte is written to disk once
    # This is the same shape as Azure block blobs: stage blocks, then commit.

    def start_upload(
        self,
        caller: Caller,
        sample_id: str,
        filename: str,
        size_bytes: int,
        content_type: str | None,
    ) -> Upload:
        sample = self._sample_for_owner(caller, sample_id)
        validate_filename(filename)
        if size_bytes > self.max_upload_bytes:
            limit_gb = self.max_upload_bytes / 1024**3
            raise PayloadTooLarge(f"file exceeds the {limit_gb:g} GB upload limit")
        if self.repo.filename_exists(sample.id, filename):
            raise Conflict("a file with this name already exists", code="duplicate_filename")
        self.purge_expired_uploads()
        if self.store.free_bytes() < size_bytes + self.free_space_reserve:
            raise InsufficientStorage("not enough free storage space for this file")

        now = self.clock.now()
        upload = Upload(
            id=str(uuid.uuid4()),
            sample_id=sample.id,
            owner_id=caller.id,
            filename=filename,
            content_type=(content_type or "application/octet-stream")[:255],
            size_bytes=size_bytes,
            received_bytes=0,
            created_at=now,
            expires_at=now + UPLOAD_SESSION_TTL,
        )
        if not self.repo.add_upload(upload):
            raise Conflict(
                "an upload with this name is already in progress", code="upload_in_progress"
            )
        self.store.create_part(upload.id)
        self.audit(
            "upload_started",
            actor_id=caller.id,
            sample_id=sample.id,
            file_id=upload.id,
            size=size_bytes,
        )
        return upload

    def get_upload(self, caller: Caller, upload_id: str) -> Upload:
        """Only the user who started an upload can see or continue it."""
        upload = self.repo.get_upload(upload_id)
        if upload is None or upload.owner_id != caller.id or upload.expires_at <= self.clock.now():
            raise NotFound("upload")
        return upload

    def write_chunk(self, caller: Caller, upload_id: str, offset: int, data: bytes) -> Upload:
        upload = self.get_upload(caller, upload_id)
        if offset != upload.received_bytes:
            # The client is out of sync (e.g. a retried request); it should resume from here.
            raise Conflict(f"expected offset {upload.received_bytes}", code="offset_mismatch")
        if not 0 < len(data) <= self.chunk_bytes:
            raise BadRequest(f"chunk must be 1 to {self.chunk_bytes} bytes", code="bad_chunk")
        end = offset + len(data)
        if end > upload.size_bytes:
            raise BadRequest("chunk goes past the declared file size", code="bad_chunk")

        try:
            self.store.write_part(upload.id, offset, data)
        except FileNotFoundError as exc:  # aborted or sample deleted while this chunk was in flight
            raise NotFound("upload") from exc
        if not self.repo.advance_upload(upload.id, offset, end):
            raise Conflict("another request wrote this chunk first", code="offset_mismatch")
        updated = self.repo.get_upload(upload.id)
        if updated is None:  # aborted or completed by a concurrent request
            raise NotFound("upload")
        return updated

    def complete_upload(self, caller: Caller, upload_id: str) -> File:
        upload = self.get_upload(caller, upload_id)
        if upload.received_bytes != upload.size_bytes:
            raise Conflict(
                f"received {upload.received_bytes} of {upload.size_bytes} bytes",
                code="upload_incomplete",
            )
        # Claim the session before touching the bytes, so a retried or concurrent complete
        # can't finalize twice: only the request that deletes the row carries on.
        if not self.repo.delete_upload(upload.id):
            raise NotFound("upload")
        try:
            size, sha256 = self.store.finalize(upload.id)
        except FileNotFoundError as exc:  # bytes lost (e.g. crash mid-complete): re-upload
            raise NotFound("upload") from exc
        file = File(
            id=upload.id,
            sample_id=upload.sample_id,
            filename=upload.filename,
            qc_status=QcStatus.PENDING,
            qc_updated_at=None,
            created_at=self.clock.now(),
            size_bytes=size,
            sha256=sha256,
            content_type=upload.content_type,
            storage_key=upload.id,
        )
        if not self.repo.add_file(file):
            self.store.delete(upload.id)
            if self.repo.get_sample(upload.sample_id) is None:  # deleted meanwhile
                raise NotFound("sample")
            raise Conflict("a file with this name already exists", code="duplicate_filename")
        self.audit(
            "file_uploaded",
            actor_id=caller.id,
            sample_id=upload.sample_id,
            file_id=file.id,
            size=size,
        )
        return file

    def abort_upload(self, caller: Caller, upload_id: str) -> None:
        upload = self.get_upload(caller, upload_id)
        self.repo.delete_upload(upload.id)
        self.store.delete_part(upload.id)
        self.audit(
            "upload_aborted", actor_id=caller.id, sample_id=upload.sample_id, file_id=upload.id
        )

    def purge_expired_uploads(self) -> int:
        """Remove abandoned uploads and their partial bytes. Returns how many were removed."""
        expired = self.repo.list_expired_uploads(self.clock.now())
        for upload in expired:
            self.repo.delete_upload(upload.id)
            self.store.delete_part(upload.id)
        return len(expired)

    # --- files ------------------------------------------------------------------------------

    def delete_file(self, caller: Caller, file_id: str) -> None:
        found = self.repo.get_file_with_sample(file_id)
        if found is None:
            raise NotFound("file")
        file, sample = found
        self._require_owner(caller, sample, resource="file")
        self.repo.delete_file(file.id)
        if file.storage_key:
            self.store.delete(file.storage_key)
        self.audit("file_deleted", actor_id=caller.id, sample_id=sample.id, file_id=file.id)

    # --- sharing ----------------------------------------------------------------------------

    def list_grants(self, caller: Caller, sample_id: str) -> list[Grant]:
        sample = self._sample_for_manager(caller, sample_id)
        return self.repo.list_grants(sample.id)

    def grant_access(self, caller: Caller, sample_id: str, username: str) -> tuple[Grant, bool]:
        """Returns (grant, created). Granting an existing grant again is a no-op."""
        sample = self._sample_for_manager(caller, sample_id)
        target = self.repo.get_user_by_username(username)
        if target is None:
            raise NotFound("user")
        if target.id == sample.owner_id:
            raise BadRequest("the owner already has access", code="cannot_grant_owner")

        grant = Grant(
            sample_id=sample.id,
            user_id=target.id,
            granted_by=caller.id,
            granted_at=self.clock.now(),
            username=target.username,
        )
        created = self.repo.add_grant(grant)
        if created:
            self.audit(
                "access_granted",
                actor_id=caller.id,
                sample_id=sample.id,
                grantee_id=target.id,
                as_admin=caller.id != sample.owner_id,
            )
        else:
            grant = self.repo.get_grant(sample.id, target.id)
        return grant, created

    def revoke_access(self, caller: Caller, sample_id: str, user_id: str) -> None:
        """Takes effect immediately, including for download URLs already issued, because
        verify_download() re-checks access every time a URL is used."""
        sample = self._sample_for_manager(caller, sample_id)
        if not self.repo.delete_grant(sample.id, user_id):
            raise NotFound("grant")
        self.audit(
            "access_revoked",
            actor_id=caller.id,
            sample_id=sample.id,
            grantee_id=user_id,
            as_admin=caller.id != sample.owner_id,
        )

    # --- QC ---------------------------------------------------------------------------------

    def update_qc(self, file_id: str, status: QcStatus) -> tuple[File, bool]:
        """pending -> passed|failed, exactly once. Returns (file, changed).

        Repeating the current terminal status is an idempotent no-op (pipelines retry).
        Asking for the other terminal status is a conflict.
        """
        if status is QcStatus.PENDING:
            raise BadRequest("QC status can only be set to passed or failed", code="invalid_status")

        if self.repo.set_qc_status_if_pending(file_id, status, self.clock.now()):
            file = self.repo.get_file(file_id)
            if file is None:  # deleted between the update and this read
                raise NotFound("file")
            self.audit(
                "qc_status_changed", sample_id=file.sample_id, file_id=file_id, qc_status=status
            )
            return file, True

        file = self.repo.get_file(file_id)
        if file is None:
            raise NotFound("file")
        if file.qc_status is status:
            return file, False
        self.audit(
            "qc_transition_rejected",
            level=logging.WARNING,
            sample_id=file.sample_id,
            file_id=file_id,
            current=file.qc_status.value,
            requested=status.value,
        )
        raise InvalidTransition(file.qc_status, status)

    # --- downloads --------------------------------------------------------------------------

    def request_download(self, caller: Caller, file_id: str) -> SignedUrl:
        file = self._authorize_download(caller.id, file_id)
        expires_at = self.clock.now() + self.url_ttl
        url = self.signer.sign(file_id, caller.id, int(expires_at.timestamp()))
        self.audit(
            "download_url_issued", actor_id=caller.id, sample_id=file.sample_id, file_id=file_id
        )
        return SignedUrl(url=url, expires_at=expires_at)

    def verify_download(self, file_id: str, user_id: str, exp: int, sig: str) -> File:
        """Called when a signed URL is used. Checks the signature, then re-checks the rule,
        so a revoked grant or deleted file stops an already-issued URL immediately."""
        now = int(self.clock.now().timestamp())
        if not self.signer.verify(file_id, user_id, exp, sig, now=now):
            self.audit("download_link_rejected", actor_id=user_id, file_id=file_id)
            raise InvalidLink()
        user = self.repo.get_user(user_id)
        if user is None or not user.is_active:  # deactivation also kills issued URLs
            self.audit("download_link_rejected", actor_id=user_id, file_id=file_id)
            raise InvalidLink()
        file = self._authorize_download(user_id, file_id)
        if file.storage_key and not self.store.path(file.storage_key).is_file():
            raise NotFound("file")  # bytes missing on disk: report it, and don't audit a download
        self.audit("download_served", actor_id=user_id, sample_id=file.sample_id, file_id=file_id)
        return file

    def _authorize_download(self, user_id: str, file_id: str) -> File:
        """The core rule: (owner or grantee) AND qc_status == passed. Admins get no bypass.

        Access is checked before QC so that outsiders can't learn a file's QC status,
        and "not yours" is the same NotFound as "doesn't exist".
        """
        found = self.repo.get_file_with_sample(file_id)
        if found is None or not self._can_access(user_id, found[1]):
            self.audit("download_denied", actor_id=user_id, file_id=file_id, reason="not_found")
            raise NotFound("file")
        file = found[0]
        if file.qc_status is not QcStatus.PASSED:
            self.audit(
                "download_denied",
                actor_id=user_id,
                file_id=file_id,
                reason=f"qc_{file.qc_status}",
            )
            raise QcNotPassed(file.qc_status)
        return file

    # --- rules ------------------------------------------------------------------------------

    def _can_access(self, user_id: str, sample: Sample) -> bool:
        return user_id == sample.owner_id or self.repo.has_grant(sample.id, user_id)

    def _access(self, caller: Caller, sample: Sample) -> str:
        if caller.id == sample.owner_id:
            return "owner"
        return "grantee" if self.repo.has_grant(sample.id, caller.id) else "none"

    def _sample_for_owner(self, caller: Caller, sample_id: str) -> Sample:
        sample = self.repo.get_sample(sample_id, with_files=True)
        if sample is None:
            raise NotFound("sample")
        self._require_owner(caller, sample, resource="sample")
        return sample

    def _require_owner(self, caller: Caller, sample: Sample, resource: str) -> None:
        if caller.id == sample.owner_id:
            return
        # Someone who can already see the sample learns nothing from a 403; anyone else
        # gets the same 404 as for something that doesn't exist.
        if caller.is_admin or self.repo.has_grant(sample.id, caller.id):
            raise Forbidden(f"only the sample owner can change this {resource}")
        raise NotFound(resource)

    def _sample_for_manager(self, caller: Caller, sample_id: str) -> Sample:
        """Owner or admin may manage sharing."""
        sample = self.repo.get_sample(sample_id)
        if sample is None:
            raise NotFound("sample")
        if caller.id == sample.owner_id or caller.is_admin:
            return sample
        if self.repo.has_grant(sample.id, caller.id):
            raise Forbidden("only the sample owner or an admin can manage access")
        raise NotFound("sample")

    @staticmethod
    def _require_admin(caller: Caller) -> None:
        if not caller.is_admin:
            raise Forbidden("admin role required", code="admin_required")
