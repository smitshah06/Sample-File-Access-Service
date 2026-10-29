"""Request-scoped dependencies: who is calling, and the service wired to a DB connection."""

import hmac
import re
from collections.abc import Iterator

from fastapi import Header, Request

from app.api.schemas import USER_ID_PATTERN
from app.domain.service import AccessService
from app.persistence.repository import SqliteRepository, connect


class Unauthorized(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


def get_caller_id(x_user_id: str | None = Header(default=None)) -> str:
    # PLACEHOLDER for real authentication. In production this validates an Entra ID bearer
    # token and returns its `oid` claim. Nothing else in the codebase reads this header.
    if x_user_id is None or not re.fullmatch(USER_ID_PATTERN, x_user_id):
        raise Unauthorized("missing_identity", "X-User-Id header is missing or malformed")
    return x_user_id


def require_pipeline_token(
    request: Request, x_pipeline_token: str | None = Header(default=None)
) -> None:
    # PLACEHOLDER for service-to-service auth (managed identity + app role, or Service Bus).
    expected = request.app.state.settings.qc_callback_token.encode()
    if x_pipeline_token is None or not hmac.compare_digest(x_pipeline_token.encode(), expected):
        raise Unauthorized("invalid_pipeline_token", "pipeline token is missing or invalid")


def get_service(request: Request) -> Iterator[AccessService]:
    state = request.app.state
    conn = connect(state.settings.db_path)
    try:
        yield AccessService(
            SqliteRepository(conn),
            state.signer,
            state.clock,
            url_ttl_seconds=state.settings.download_url_ttl_seconds,
        )
    finally:
        conn.close()
