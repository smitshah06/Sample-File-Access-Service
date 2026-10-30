"""Thin HTTP handlers for samples, files, sharing, QC and downloads.

Each handler validates input, calls one service method and shapes the response. Domain errors
become status codes in app/api/errors.py.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import get_access_service, get_current_user, require_pipeline_token
from app.api.schemas import (
    DownloadUrlOut,
    FileOut,
    GrantCreate,
    GrantOut,
    QcUpdate,
    QcUpdateOut,
    SampleCreate,
    SampleOut,
    SampleSummaryOut,
    UploadCreate,
    UploadOut,
)
from app.domain.errors import PayloadTooLarge
from app.domain.models import Caller, File, Grant, QcStatus, Sample, SampleSummary, Upload
from app.domain.service import AccessService

router = APIRouter()

CurrentUser = Annotated[Caller, Depends(get_current_user)]
Service = Annotated[AccessService, Depends(get_access_service)]


def upload_out(up: Upload, service: AccessService) -> UploadOut:
    return UploadOut(
        id=up.id,
        sample_id=up.sample_id,
        filename=up.filename,
        size_bytes=up.size_bytes,
        received_bytes=up.received_bytes,
        chunk_bytes=service.chunk_bytes,
        expires_at=up.expires_at,
    )


def file_out(f: File) -> FileOut:
    return FileOut(
        id=f.id,
        filename=f.filename,
        qc_status=f.qc_status.value,
        qc_updated_at=f.qc_updated_at,
        created_at=f.created_at,
        has_content=f.has_content,
        size_bytes=f.size_bytes,
        sha256=f.sha256,
        content_type=f.content_type,
    )


def sample_out(s: Sample, access: str) -> SampleOut:
    return SampleOut(
        id=s.id,
        owner_id=s.owner_id,
        owner_username=s.owner_username,
        name=s.name,
        created_at=s.created_at,
        access=access,
        files=[file_out(f) for f in s.files],
    )


def summary_out(s: SampleSummary) -> SampleSummaryOut:
    return SampleSummaryOut(**s.__dict__)


def grant_out(g: Grant) -> GrantOut:
    return GrantOut(
        sample_id=g.sample_id,
        user_id=g.user_id,
        username=g.username,
        granted_by=g.granted_by,
        granted_at=g.granted_at,
    )


@router.api_route("/healthz", methods=["GET", "HEAD"])
def healthz(service: Service) -> dict[str, str]:
    service.repo.ping()
    return {"status": "ok"}


# --- samples --------------------------------------------------------------------------------


@router.get("/samples")
def list_samples(caller: CurrentUser, service: Service) -> list[SampleSummaryOut]:
    return [summary_out(s) for s in service.list_samples(caller)]


@router.post("/samples", status_code=status.HTTP_201_CREATED)
def register_sample(
    body: SampleCreate, caller: CurrentUser, service: Service, response: Response
) -> SampleOut:
    sample = service.register_sample(caller, body.name, [f.filename for f in body.files])
    response.headers["Location"] = f"/api/samples/{sample.id}"
    return sample_out(sample, "owner")


@router.get("/samples/{sample_id}")
def get_sample(sample_id: UUID, caller: CurrentUser, service: Service) -> SampleOut:
    sample, access = service.get_sample(caller, str(sample_id))
    return sample_out(sample, access)


@router.delete("/samples/{sample_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sample(sample_id: UUID, caller: CurrentUser, service: Service) -> None:
    service.delete_sample(caller, str(sample_id))


# --- files ----------------------------------------------------------------------------------


@router.post("/samples/{sample_id}/uploads", status_code=status.HTTP_201_CREATED)
def start_upload(
    sample_id: UUID, body: UploadCreate, caller: CurrentUser, service: Service
) -> UploadOut:
    """Start a resumable upload: declares the name and size up front, so the size cap and
    free-space check happen before any bytes are sent."""
    up = service.start_upload(
        caller, str(sample_id), body.filename, body.size_bytes, body.content_type
    )
    return upload_out(up, service)


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: UUID, caller: CurrentUser, service: Service) -> UploadOut:
    """Where to resume from after an interruption (received_bytes)."""
    return upload_out(service.get_upload(caller, str(upload_id)), service)


@router.put("/uploads/{upload_id}")
async def upload_chunk(
    upload_id: UUID,
    offset: Annotated[int, Query(ge=0)],
    request: Request,
    caller: CurrentUser,
    service: Service,
) -> UploadOut:
    """Append one chunk (raw bytes, not multipart) at `offset`. The body is read with a hard
    cap, so a request can never buffer more than one chunk in memory."""
    # Fail fast, before reading any body: unknown or someone else's upload, or a declared
    # chunk that is already too big.
    await run_in_threadpool(service.get_upload, caller, str(upload_id))
    if int(request.headers.get("content-length") or 0) > service.chunk_bytes:
        raise PayloadTooLarge(f"chunk exceeds {service.chunk_bytes} bytes", code="bad_chunk")
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > service.chunk_bytes:
            raise PayloadTooLarge(f"chunk exceeds {service.chunk_bytes} bytes", code="bad_chunk")
    # File and DB I/O are blocking: run them off the event loop.
    up = await run_in_threadpool(service.write_chunk, caller, str(upload_id), offset, bytes(data))
    return upload_out(up, service)


@router.post("/uploads/{upload_id}/complete", status_code=status.HTTP_201_CREATED)
def complete_upload(upload_id: UUID, caller: CurrentUser, service: Service) -> FileOut:
    """Verify every byte arrived, compute the SHA-256 and turn the upload into a file."""
    return file_out(service.complete_upload(caller, str(upload_id)))


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(upload_id: UUID, caller: CurrentUser, service: Service) -> None:
    service.abort_upload(caller, str(upload_id))


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: UUID, caller: CurrentUser, service: Service) -> None:
    service.delete_file(caller, str(file_id))


# --- sharing --------------------------------------------------------------------------------


@router.get("/samples/{sample_id}/grants")
def list_grants(sample_id: UUID, caller: CurrentUser, service: Service) -> list[GrantOut]:
    return [grant_out(g) for g in service.list_grants(caller, str(sample_id))]


@router.post("/samples/{sample_id}/grants", status_code=status.HTTP_201_CREATED)
def grant_access(
    sample_id: UUID, body: GrantCreate, caller: CurrentUser, service: Service, response: Response
) -> GrantOut:
    grant, created = service.grant_access(caller, str(sample_id), body.username)
    if not created:
        response.status_code = status.HTTP_200_OK
    return grant_out(grant)


@router.delete("/samples/{sample_id}/grants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_access(sample_id: UUID, user_id: UUID, caller: CurrentUser, service: Service) -> None:
    service.revoke_access(caller, str(sample_id), str(user_id))


# --- QC (internal pipeline) -----------------------------------------------------------------


@router.put("/internal/files/{file_id}/qc-status", dependencies=[Depends(require_pipeline_token)])
def update_qc_status(file_id: UUID, body: QcUpdate, service: Service) -> QcUpdateOut:
    file, changed = service.update_qc(str(file_id), QcStatus(body.status))
    return QcUpdateOut(
        file_id=file.id,
        qc_status=file.qc_status.value,
        changed=changed,
        qc_updated_at=file.qc_updated_at,
    )


# --- downloads ------------------------------------------------------------------------------


@router.post("/files/{file_id}/download-url")
def request_download_url(
    file_id: UUID, caller: CurrentUser, service: Service, response: Response
) -> DownloadUrlOut:
    signed = service.request_download(caller, str(file_id))
    response.headers["Cache-Control"] = "no-store"  # a signed URL is a credential
    return DownloadUrlOut(url=signed.url, expires_at=signed.expires_at)


@router.get("/downloads/{file_id}", response_model=None)  # GET only: every use is audited
def download(
    file_id: UUID, user: UUID, exp: int, sig: str, service: Service
) -> FileResponse | PlainTextResponse:
    """Stands in for blob storage: the signed URL is the credential (no session needed),
    like an Azure SAS URL. Access and QC are re-checked on every use."""
    file = service.verify_download(str(file_id), str(user), exp, sig)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if not file.has_content:
        return PlainTextResponse(
            "placeholder: this file was registered by metadata only; no content was uploaded\n",
            headers=headers,
        )
    # Always an attachment with a generic type, so an uploaded HTML/SVG file can never
    # render (and run script) in the browser on this origin.
    return FileResponse(
        service.store.path(file.storage_key),
        media_type="application/octet-stream",
        filename=file.filename,
        headers=headers,
    )
