import os
from dataclasses import dataclass

MIN_SECRET_LENGTH = 32


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    signing_secret: str
    qc_callback_token: str
    admin_username: str = ""
    admin_password: str = ""
    db_path: str = "sample_access.db"
    storage_dir: str = "storage"
    public_base_url: str = "http://localhost:8000"
    download_url_ttl_seconds: int = 300
    session_ttl_hours: int = 8
    cookie_secure: bool = True
    max_upload_mb: int = 10 * 1024  # 10 GB local cap
    upload_chunk_bytes: int = 16 * 1024 * 1024
    min_free_disk_mb: int = 1024  # uploads are refused rather than fill the disk
    frontend_dist: str = "frontend/dist"
    enable_docs: bool = True  # /api/docs; turn off in production

    def __post_init__(self):
        # Fail fast: refuse to start with missing or weak secrets rather than run insecurely.
        for name in ("signing_secret", "qc_callback_token"):
            if len(getattr(self, name) or "") < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"{name.upper()} must be set and at least {MIN_SECRET_LENGTH} characters"
                )
        if self.signing_secret == self.qc_callback_token:
            raise ValueError("SIGNING_SECRET and QC_CALLBACK_TOKEN must be different")
        if bool(self.admin_username) != bool(self.admin_password):
            raise ValueError("set both ADMIN_USERNAME and ADMIN_PASSWORD, or neither")
        for name in (
            "download_url_ttl_seconds",
            "session_ttl_hours",
            "max_upload_mb",
            "upload_chunk_bytes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be positive")
        if self.min_free_disk_mb < 0:
            raise ValueError("MIN_FREE_DISK_MB must not be negative")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            signing_secret=env("SIGNING_SECRET", ""),
            qc_callback_token=env("QC_CALLBACK_TOKEN", ""),
            admin_username=env("ADMIN_USERNAME", ""),
            admin_password=env("ADMIN_PASSWORD", ""),
            db_path=env("DB_PATH", cls.db_path),
            storage_dir=env("STORAGE_DIR", cls.storage_dir),
            public_base_url=env("PUBLIC_BASE_URL", cls.public_base_url),
            download_url_ttl_seconds=int(
                env("DOWNLOAD_URL_TTL_SECONDS", cls.download_url_ttl_seconds)
            ),
            session_ttl_hours=int(env("SESSION_TTL_HOURS", cls.session_ttl_hours)),
            cookie_secure=_bool(env("COOKIE_SECURE", "true")),
            max_upload_mb=int(env("MAX_UPLOAD_MB", cls.max_upload_mb)),
            upload_chunk_bytes=int(env("UPLOAD_CHUNK_BYTES", cls.upload_chunk_bytes)),
            min_free_disk_mb=int(env("MIN_FREE_DISK_MB", cls.min_free_disk_mb)),
            frontend_dist=env("FRONTEND_DIST", cls.frontend_dist),
            enable_docs=_bool(env("ENABLE_DOCS", "true")),
        )
