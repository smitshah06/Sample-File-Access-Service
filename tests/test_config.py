"""The service refuses to start with missing or weak secrets rather than run insecurely."""

import pytest

from app.config import Settings

GOOD = {"signing_secret": "s" * 32, "qc_callback_token": "t" * 32}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"signing_secret": ""}, "SIGNING_SECRET must be set"),
        ({"signing_secret": "s" * 31}, "SIGNING_SECRET must be set"),
        ({"qc_callback_token": ""}, "QC_CALLBACK_TOKEN must be set"),
        ({"qc_callback_token": "s" * 32}, "must be different"),
        ({"admin_username": "admin"}, "ADMIN_USERNAME and ADMIN_PASSWORD"),
        ({"download_url_ttl_seconds": 0}, "DOWNLOAD_URL_TTL_SECONDS must be positive"),
        ({"min_free_disk_mb": -1}, "MIN_FREE_DISK_MB must not be negative"),
    ],
)
def test_invalid_settings_fail_fast(overrides, message):
    with pytest.raises(ValueError, match=message):
        Settings(**{**GOOD, **overrides})


def test_from_env_reads_values_and_rejects_missing_secrets(monkeypatch):
    monkeypatch.delenv("SIGNING_SECRET", raising=False)
    with pytest.raises(ValueError, match="SIGNING_SECRET"):
        Settings.from_env()

    monkeypatch.setenv("SIGNING_SECRET", "s" * 32)
    monkeypatch.setenv("QC_CALLBACK_TOKEN", "t" * 32)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    settings = Settings.from_env()
    assert (settings.cookie_secure, settings.max_upload_mb) == (False, 5)
