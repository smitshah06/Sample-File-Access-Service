import os
from dataclasses import dataclass

MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class Settings:
    signing_secret: str
    qc_callback_token: str
    db_path: str = "sample_access.db"
    public_base_url: str = "http://localhost:8000"
    download_url_ttl_seconds: int = 300

    def __post_init__(self):
        # Fail fast: refuse to start with missing or weak secrets rather than run insecurely.
        for name in ("signing_secret", "qc_callback_token"):
            if len(getattr(self, name) or "") < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"{name.upper()} must be set and at least {MIN_SECRET_LENGTH} characters"
                )
        if self.signing_secret == self.qc_callback_token:
            raise ValueError("SIGNING_SECRET and QC_CALLBACK_TOKEN must be different")
        if self.download_url_ttl_seconds <= 0:
            raise ValueError("DOWNLOAD_URL_TTL_SECONDS must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            signing_secret=os.environ.get("SIGNING_SECRET", ""),
            qc_callback_token=os.environ.get("QC_CALLBACK_TOKEN", ""),
            db_path=os.environ.get("DB_PATH", cls.db_path),
            public_base_url=os.environ.get("PUBLIC_BASE_URL", cls.public_base_url),
            download_url_ttl_seconds=int(
                os.environ.get("DOWNLOAD_URL_TTL_SECONDS", cls.download_url_ttl_seconds)
            ),
        )
