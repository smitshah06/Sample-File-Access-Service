"""Thin HTTP handlers: validate input, call one service method, shape the response.

Domain errors are turned into status codes in exactly one place (ERROR_STATUS below).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import Unauthorized, get_caller_id, get_service, require_pipeline_token
from app.api.schemas import (
    DownloadOut,
    DownloadUrlOut,
    FileOut,
    GrantCreate,
    GrantOut,
    QcUpdate,
    QcUpdateOut,
    SampleCreate,
    SampleOut,
)
from app.domain.errors import (
    BadRequest,
    DomainError,
    Forbidden,
    InvalidLink,
    InvalidTransition,
    NotFound,
    QcNotPassed,
)
from app.domain.models import File, Grant, QcStatus, Sample
from app.domain.service import AccessService

router = APIRouter()

CallerId = Annotated[str, Depends(get_caller_id)]
Service = Annotated[AccessService, Depends(get_service)]


def _file_out(f: File) -> FileOut:
    return FileOut(
        id=f.id, filename=f.filename, qc_status=f.qc_status.value, qc_updated_at=f.qc_updated_at
    )


def _sample_out(s: Sample) -> SampleOut:
    return SampleOut(
        id=s.id,
        owner_id=s.owner_id,
        name=s.name,
        created_at=s.created_at,
        files=[_file_out(f) for f in s.files],
    )


def _grant_out(g: Grant) -> GrantOut:
    return GrantOut(
        sample_id=g.sample_id, user_id=g.user_id, granted_by=g.granted_by, granted_at=g.granted_at
    )


@router.get("/healthz")
def healthz(service: Service) -> dict[str, str]:
    service.repo.ping()
    return {"status": "ok"}


@router.post("/samples", status_code=status.HTTP_201_CREATED)
def register_sample(
    body: SampleCreate, caller_id: CallerId, service: Service, response: Response
) -> SampleOut:
    sample = service.register_sample(caller_id, body.name, [f.filename for f in body.files])
    response.headers["Location"] = f"/samples/{sample.id}"
    return _sample_out(sample)


@router.get("/samples/{sample_id}")
def get_sample(sample_id: UUID, caller_id: CallerId, service: Service) -> SampleOut:
    return _sample_out(service.get_sample(caller_id, str(sample_id)))


@router.post("/samples/{sample_id}/grants", status_code=status.HTTP_201_CREATED)
def grant_access(
    sample_id: UUID, body: GrantCreate, caller_id: CallerId, service: Service, response: Response
) -> GrantOut:
    grant, created = service.grant_access(caller_id, str(sample_id), body.user_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return _grant_out(grant)


@router.put("/internal/files/{file_id}/qc-status", dependencies=[Depends(require_pipeline_token)])
def update_qc_status(file_id: UUID, body: QcUpdate, service: Service) -> QcUpdateOut:
    file, changed = service.update_qc(str(file_id), QcStatus(body.status))
    return QcUpdateOut(
        file_id=file.id,
        qc_status=file.qc_status.value,
        changed=changed,
        qc_updated_at=file.qc_updated_at,
    )


@router.post("/files/{file_id}/download-url")
def request_download_url(
    file_id: UUID, caller_id: CallerId, service: Service, response: Response
) -> DownloadUrlOut:
    signed = service.request_download(caller_id, str(file_id))
    response.headers["Cache-Control"] = "no-store"  # a signed URL is a credential
    return DownloadUrlOut(url=signed.url, expires_at=signed.expires_at)


@router.get("/downloads/{file_id}")
def download(file_id: UUID, user: str, exp: int, sig: str, service: Service) -> DownloadOut:
    """Stands in for blob storage: accepts only a valid, unexpired signed URL."""
    file = service.verify_download(str(file_id), user, exp, sig)
    return DownloadOut(file_id=file.id, note="fake file content")


# --- error mapping --------------------------------------------------------------------------

ERROR_STATUS: dict[type[DomainError], int] = {
    BadRequest: status.HTTP_400_BAD_REQUEST,
    Forbidden: status.HTTP_403_FORBIDDEN,
    InvalidLink: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    QcNotPassed: status.HTTP_409_CONFLICT,
    InvalidTransition: status.HTTP_409_CONFLICT,
}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status_code = ERROR_STATUS.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        return _error(status_code, exc.code, exc.message)

    @app.exception_handler(Unauthorized)
    async def _unauthorized(_: Request, exc: Unauthorized) -> JSONResponse:
        return _error(status.HTTP_401_UNAUTHORIZED, exc.code, exc.message)
