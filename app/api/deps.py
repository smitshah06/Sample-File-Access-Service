"""Request-scoped dependencies: one DB connection per request, the services built on it,
and who is calling."""

import hmac
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request

from app.domain.audit import Auditor
from app.domain.errors import Unauthenticated
from app.domain.models import Caller
from app.domain.service import AccessService
from app.domain.users import UserService
from app.persistence.repository import SqliteRepository, connect

SESSION_COOKIE = "sfa_session"


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _auditor(request: Request, repo: SqliteRepository) -> Auditor:
    return Auditor(repo, request.app.state.clock)


def get_access_service(request: Request, conn: Conn) -> AccessService:
    state = request.app.state
    repo = SqliteRepository(conn)
    return AccessService(
        repo,
        state.signer,
        state.clock,
        _auditor(request, repo),
        state.store,
        url_ttl_seconds=state.settings.download_url_ttl_seconds,
        max_upload_bytes=state.settings.max_upload_mb * 1024 * 1024,
        chunk_bytes=state.settings.upload_chunk_bytes,
        free_space_reserve=state.settings.min_free_disk_mb * 1024 * 1024,
    )


def get_user_service(request: Request, conn: Conn) -> UserService:
    repo = SqliteRepository(conn)
    return UserService(
        repo,
        request.app.state.clock,
        _auditor(request, repo),
        session_ttl_hours=request.app.state.settings.session_ttl_hours,
    )


Users = Annotated[UserService, Depends(get_user_service)]


def get_current_user(
    users: Users, session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None
) -> Caller:
    # The single identity seam. In production this validates a Microsoft Entra ID token
    # instead; nothing else in the codebase knows how the caller was authenticated.
    caller = users.resolve_session(session) if session else None
    if caller is None:
        raise Unauthenticated("sign in required")
    return caller


def require_pipeline_token(
    request: Request, x_pipeline_token: str | None = Header(default=None)
) -> None:
    # PLACEHOLDER for service-to-service auth (managed identity + app role, or Service Bus).
    expected = request.app.state.settings.qc_callback_token.encode()
    if x_pipeline_token is None or not hmac.compare_digest(x_pipeline_token.encode(), expected):
        raise Unauthenticated("pipeline token is missing or invalid", code="invalid_pipeline_token")
