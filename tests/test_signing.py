from urllib.parse import parse_qs, urlparse

import pytest

from app.signing import UrlSigner

FILE_ID = "6f1e2c3a-0000-4000-8000-000000000001"
NOW = 1_800_000_000
EXP = NOW + 300


def _sig(signer):
    return parse_qs(urlparse(signer.sign(FILE_ID, "alice", EXP)).query)["sig"][0]


def test_valid_signature_before_expiry(signer):  # S1
    assert signer.verify(FILE_ID, "alice", EXP, _sig(signer), now=NOW)


def test_expired_url_is_rejected(signer):  # S2
    sig = _sig(signer)

    assert signer.verify(FILE_ID, "alice", EXP, sig, now=EXP - 1)
    assert not signer.verify(FILE_ID, "alice", EXP, sig, now=EXP)
    assert not signer.verify(FILE_ID, "alice", EXP, sig, now=EXP + 3600)


@pytest.mark.parametrize(
    ("file_id", "user", "exp", "sig_override"),
    [
        ("6f1e2c3a-0000-4000-8000-000000000002", "alice", EXP, None),  # other file
        (FILE_ID, "mallory", EXP, None),  # other user
        (FILE_ID, "alice", EXP + 86_400, None),  # extended expiry
        (FILE_ID, "alice", EXP, "AAAA"),  # forged signature
        (FILE_ID, "alice", EXP, "é"),  # non-ASCII input must not crash
    ],
)
def test_tampered_url_is_rejected(signer, file_id, user, exp, sig_override):  # S3
    sig = sig_override or _sig(signer)

    assert not signer.verify(file_id, user, exp, sig, now=NOW)


def test_signature_from_another_secret_is_rejected(signer):  # S4
    other = UrlSigner("a-completely-different-secret-value-xyz", "http://testserver")

    assert not signer.verify(FILE_ID, "alice", EXP, _sig(other), now=NOW)


def test_url_shape(signer):
    url = urlparse(signer.sign(FILE_ID, "alice@lab", EXP))

    assert url.path == f"/downloads/{FILE_ID}"
    assert parse_qs(url.query)["user"] == ["alice@lab"]
