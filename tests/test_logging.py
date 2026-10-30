"""Signed URLs must never reach the logs."""

import logging


def test_access_log_redacts_download_signatures(app, caplog):
    access = logging.getLogger("uvicorn.access")
    path = "/api/downloads/f1?user=u1&exp=1793309004&sig=QoP6TbSECRETsignature"

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        # The exact call uvicorn makes for each request.
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 200)

    line = caplog.records[-1].getMessage()
    assert "SECRET" not in line
    assert "sig=REDACTED" in line
    assert "user=u1&exp=1793309004" in line  # everything else is kept for troubleshooting
