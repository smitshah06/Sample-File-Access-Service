"""Domain errors become HTTP status codes here, and only here."""

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.routing import Match

from app.domain.errors import (
    BadRequest,
    Conflict,
    DomainError,
    Forbidden,
    InsufficientStorage,
    InvalidLink,
    InvalidTransition,
    NotFound,
    PayloadTooLarge,
    QcNotPassed,
    Unauthenticated,
)

ERROR_STATUS: dict[type[DomainError], int] = {
    BadRequest: status.HTTP_400_BAD_REQUEST,
    Unauthenticated: status.HTTP_401_UNAUTHORIZED,
    Forbidden: status.HTTP_403_FORBIDDEN,
    InvalidLink: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    QcNotPassed: status.HTTP_409_CONFLICT,
    InvalidTransition: status.HTTP_409_CONFLICT,
    PayloadTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    InsufficientStorage: status.HTTP_507_INSUFFICIENT_STORAGE,
}


HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}
METHODS = ("DELETE", "GET", "PATCH", "POST", "PUT")


def allowed_methods(app: FastAPI, path: str, skip: object = None) -> list[str]:
    """Every method some route accepts for this path (Starlette only reports the first)."""
    routes = [
        r for r in app.router.routes if skip is None or getattr(r, "endpoint", None) is not skip
    ]
    # A fresh scope: by the time an error handler runs, routing has rewritten the original.
    scope = {"type": "http", "path": path, "root_path": "", "headers": []}
    return [
        m
        for m in METHODS
        if any(r.matches({**scope, "method": m})[0] is Match.FULL for r in routes)
    ]


def error_response(
    status_code: int, code: str, message: str, details: list | None = None
) -> JSONResponse:
    body: dict = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body})


def _describe(error: dict) -> str:
    """'name: String should have at least 1 character', or just the message when the error
    isn't about a named field (e.g. malformed JSON)."""
    field = ".".join(str(part) for part in error["loc"][1:] if isinstance(part, str))
    return f"{field}: {error['msg']}" if field else error["msg"]


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status_code = ERROR_STATUS.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
        return error_response(status_code, exc.code, exc.message)

    # Framework errors use the same {"error": {...}} shape, so clients parse one format.
    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        message = "; ".join(_describe(e) for e in errors)
        details = jsonable_encoder(errors, exclude={"input", "ctx", "url"})
        return error_response(422, "validation_error", message, details)

    # Anything unexpected: same shape, no details (the server logs the traceback).
    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        return error_response(500, "internal_error", "internal server error")

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(
            exc.status_code, HTTP_CODES.get(exc.status_code, "http_error"), str(exc.detail)
        )
        response.headers.update(exc.headers or {})
        if exc.status_code == 405:
            response.headers["Allow"] = ", ".join(allowed_methods(app, request.url.path))
        return response
