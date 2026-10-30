"""App factory. Run with: uvicorn app.main:create_app --factory"""

import logging
import re
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, Response

from app.api import routes_admin, routes_auth, routes_samples
from app.api.errors import allowed_methods, error_response, register_error_handlers
from app.clock import Clock, SystemClock
from app.config import Settings
from app.domain.audit import Auditor
from app.domain.errors import DomainError
from app.domain.users import UserService
from app.persistence.repository import SqliteRepository, connect, init_schema
from app.signing import UrlSigner
from app.storage import LocalDiskStore

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-requested-with"
# On every response: no MIME sniffing, no framing (clickjacking), no URLs leaked via Referer.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
# Only on the UI: everything from our own origin, nothing inline or third-party. (Not on
# /api/docs, which loads Swagger UI from a CDN; turn docs off with ENABLE_DOCS=false.)
SPA_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
}


_SIGNATURE = re.compile(r"(\bsig=)[^&\s\"]+")


class RedactSignatures(logging.Filter):
    """Signed download URLs are bearer credentials: keep their signatures out of access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SIGNATURE.sub(r"\1REDACTED", a) if isinstance(a, str) else a for a in record.args
            )
        return True


def _configure_logging() -> None:
    audit = logging.getLogger("audit")
    if not audit.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))  # message is already JSON
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        audit.propagate = False
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactSignatures) for f in access.filters):
        access.addFilter(RedactSignatures())


def _init_db(settings: Settings, clock: Clock) -> None:
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        if settings.admin_username:
            repo = SqliteRepository(conn)
            try:
                UserService(repo, clock, Auditor(repo, clock)).seed_admin(
                    settings.admin_username, settings.admin_password
                )
            except DomainError as exc:  # e.g. the name already belongs to a non-admin user
                raise RuntimeError(
                    f"cannot create the first admin '{settings.admin_username}': {exc.message}"
                ) from exc
    finally:
        conn.close()


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built single-page app at / so UI and API share one origin."""
    index = dist / "index.html"
    if not index.is_file():
        return
    root = dist.resolve()

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD"], include_in_schema=False, response_model=None
    )
    def spa(path: str, request: Request) -> Response:
        if path == "api" or path.startswith("api/"):
            # This catch-all only sees GETs that no API route took. If the path exists with
            # other methods, say so (405) instead of pretending it doesn't exist.
            allowed = allowed_methods(app, request.url.path, skip=spa)
            if allowed:
                response = error_response(405, "method_not_allowed", "method not allowed")
                response.headers["Allow"] = ", ".join(allowed)
                return response
            return error_response(404, "not_found", "no such API route")
        if "\x00" in path:  # Path.resolve() raises on NUL bytes; no such file can exist
            return error_response(404, "not_found", "not found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate, headers=SPA_HEADERS)
        return FileResponse(index, headers=SPA_HEADERS)  # client-side routes like /samples/123


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    clock = clock or SystemClock()
    _configure_logging()
    _init_db(settings, clock)

    app = FastAPI(
        title="Sample File Access Service",
        description="Download URLs for research data files, gated on access grants and QC.",
        version="0.2.0",
        docs_url="/api/docs" if settings.enable_docs else None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.signer = UrlSigner(
        settings.signing_secret, settings.public_base_url.rstrip("/") + "/api"
    )
    app.state.store = LocalDiskStore(settings.storage_dir)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next) -> Response:
        # Browsers can't add a custom header to a cross-site request without a CORS preflight,
        # and no CORS is enabled, so requiring one blocks cross-site form posts. Together with
        # SameSite=Strict cookies this is the CSRF defence. The pipeline route uses a token,
        # not a cookie, so it is exempt.
        path = request.url.path
        if (
            request.method not in SAFE_METHODS
            and path.startswith("/api/")
            and not path.startswith("/api/internal/")
            and request.headers.get(CSRF_HEADER) != "fetch"
        ):
            response = error_response(403, "csrf_check_failed", "missing X-Requested-With header")
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    api = APIRouter(prefix="/api")
    api.include_router(routes_auth.router)
    api.include_router(routes_admin.router)
    api.include_router(routes_samples.router)
    app.include_router(api)
    register_error_handlers(app)
    _mount_frontend(app, Path(settings.frontend_dist))
    return app
