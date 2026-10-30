"""HMAC-signed, time-limited download URLs.

The signature covers file ID, user ID and expiry, so changing any of them invalidates it.
Same shape as an Azure Blob SAS URL (resource + expiry + signature), which is the
production replacement for this class.
"""

import base64
import hashlib
import hmac
from urllib.parse import urlencode

VERSION = "v1"


class UrlSigner:
    def __init__(self, secret: str, base_url: str):
        self._key = secret.encode()
        self._base_url = base_url.rstrip("/")

    def _signature(self, file_id: str, user_id: str, exp: int) -> str:
        # File and user IDs are UUIDs (no ":") and exp is an int, so the payload is unambiguous.
        payload = f"{VERSION}:{file_id}:{user_id}:{exp}".encode()
        digest = hmac.new(self._key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def sign(self, file_id: str, user_id: str, exp: int) -> str:
        query = urlencode(
            {"user": user_id, "exp": exp, "sig": self._signature(file_id, user_id, exp)}
        )
        return f"{self._base_url}/downloads/{file_id}?{query}"

    def verify(self, file_id: str, user_id: str, exp: int, sig: str, now: int) -> bool:
        expected = self._signature(file_id, user_id, exp)
        # compare_digest runs in constant time, so response timing doesn't reveal the signature.
        valid = hmac.compare_digest(expected.encode(), sig.encode())
        return valid and now < exp
