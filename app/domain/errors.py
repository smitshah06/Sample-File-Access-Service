"""Domain errors. The API layer maps each type to one HTTP status code."""

from app.domain.models import QcStatus


class DomainError(Exception):
    code = "error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class Unauthenticated(DomainError):
    code = "not_authenticated"


class NotFound(DomainError):
    """Resource doesn't exist, or the caller may not know it exists. Deliberately the same."""

    def __init__(self, resource: str):
        super().__init__(f"{resource} not found", code=f"{resource}_not_found")


class Forbidden(DomainError):
    code = "forbidden"


class BadRequest(DomainError):
    code = "bad_request"


class Conflict(DomainError):
    code = "conflict"


class PayloadTooLarge(DomainError):
    code = "file_too_large"


class InsufficientStorage(DomainError):
    code = "insufficient_storage"


class QcNotPassed(DomainError):
    def __init__(self, status: QcStatus):
        super().__init__(f"file QC status is '{status}'", code=f"qc_{status}")
        self.status = status


class InvalidTransition(DomainError):
    code = "invalid_transition"

    def __init__(self, current: QcStatus, requested: QcStatus):
        super().__init__(f"QC status is already '{current}'; cannot change it to '{requested}'")
        self.current = current
        self.requested = requested


class InvalidLink(DomainError):
    """One error for bad signature, tampered params and expiry, so callers can't tell which."""

    code = "invalid_or_expired_link"

    def __init__(self):
        super().__init__("download link is invalid or has expired")
