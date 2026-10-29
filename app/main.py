"""App factory. Run with: uvicorn app.main:create_app --factory"""

import logging
import sys

from fastapi import FastAPI

from app.api.routes import register_error_handlers, router
from app.clock import Clock, SystemClock
from app.config import Settings
from app.persistence.repository import connect, init_schema
from app.signing import UrlSigner


def _configure_audit_log() -> None:
    audit = logging.getLogger("audit")
    if not audit.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))  # message is already JSON
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        audit.propagate = False


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    _configure_audit_log()

    conn = connect(settings.db_path)
    try:
        init_schema(conn)
    finally:
        conn.close()

    app = FastAPI(
        title="Sample File Access Service",
        description="Download URLs for research data files, gated on access grants and QC.",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.signer = UrlSigner(settings.signing_secret, settings.public_base_url)
    app.state.clock = clock or SystemClock()
    app.include_router(router)
    register_error_handlers(app)
    return app
