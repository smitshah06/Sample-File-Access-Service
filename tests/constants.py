"""Values shared by test modules (importing conftest directly is a pytest anti-pattern)."""

BASE_URL = "http://testserver"
PASSWORD = "correct-horse-battery"
ADMIN = "admin"
CSRF = {"X-Requested-With": "fetch"}
MAX_UPLOAD_BYTES = 1024
CHUNK_BYTES = 100
