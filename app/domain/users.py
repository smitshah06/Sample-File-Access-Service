"""Accounts, passwords and sessions. Local accounts stand in for Microsoft Entra ID."""

import base64
import hashlib
import hmac
import logging
import re
import secrets
import uuid
from datetime import timedelta

from app.clock import Clock
from app.domain.audit import Auditor
from app.domain.errors import BadRequest, Conflict, Forbidden, NotFound, Unauthenticated
from app.domain.models import Caller, Role, User
from app.persistence.repository import SqliteRepository

USERNAME_PATTERN = r"^[A-Za-z0-9._@-]{3,64}$"
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
LOCK_AFTER_FAILURES = 5
LOCK_DURATION = timedelta(minutes=15)

# scrypt: memory-hard, in the standard library. n=2^14, r=8 uses 16 MiB per hash.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, expected = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, base64.b64decode(expected))


# Verified against when the username doesn't exist, so a login takes the same time either way
# and response timing can't be used to find valid usernames.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _caller(user: User) -> Caller:
    return Caller(id=user.id, username=user.username, role=user.role)


class UserService:
    def __init__(
        self, repo: SqliteRepository, clock: Clock, audit: Auditor, session_ttl_hours: int = 8
    ):
        self.repo = repo
        self.clock = clock
        self.audit = audit
        self.session_ttl = timedelta(hours=session_ttl_hours)

    # --- sign-in and sessions ---------------------------------------------------------------

    def login(self, username: str, password: str) -> tuple[Caller, str]:
        """Returns (caller, session token). Every failure is the same error, so the response
        never reveals whether the username exists, is deactivated or is locked."""
        invalid = Unauthenticated("invalid username or password", code="invalid_credentials")
        now = self.clock.now()
        user = self.repo.get_user_by_username(username)

        if user is None:
            verify_password(password, _DUMMY_HASH)
            self.audit("login_failed", reason="unknown_user")
            raise invalid
        if user.locked_until is not None and user.locked_until > now:
            verify_password(password, _DUMMY_HASH)
            self.audit("login_failed", actor_id=user.id, reason="locked")
            raise invalid
        if not verify_password(password, user.password_hash):
            # A failure more than LOCK_DURATION after the previous one starts a new count,
            # so occasional old mistakes don't add up to a lockout.
            locked = self.repo.record_login_failure(
                user.id, now, now - LOCK_DURATION, LOCK_AFTER_FAILURES, now + LOCK_DURATION
            )
            self.audit(
                "account_locked" if locked else "login_failed",
                actor_id=user.id,
                level=logging.WARNING if locked else logging.INFO,
                reason="bad_password",
            )
            raise invalid
        if not user.is_active:
            self.audit("login_failed", actor_id=user.id, reason="inactive")
            raise invalid

        self.repo.reset_login_failures(user.id)
        self.repo.delete_expired_sessions(now)  # housekeeping: expired rows are useless
        token = secrets.token_urlsafe(32)
        self.repo.add_session(_token_hash(token), user.id, now, now + self.session_ttl)
        self.audit("login_succeeded", actor_id=user.id)
        return _caller(user), token

    def resolve_session(self, token: str) -> Caller | None:
        user = self.repo.get_session_user(_token_hash(token), self.clock.now())
        return _caller(user) if user is not None else None

    def logout(self, token: str) -> None:
        caller = self.resolve_session(token)
        self.repo.delete_session(_token_hash(token))
        if caller is not None:
            self.audit("logout", actor_id=caller.id)

    # --- user administration (admin only) ---------------------------------------------------

    def seed_admin(self, username: str, password: str) -> bool:
        """Create the first admin if none exists yet. Returns True if one was created."""
        if self.repo.any_admin():
            return False
        self._create(username, password, Role.ADMIN, actor_id=None)
        return True

    def create_user(self, caller: Caller, username: str, password: str, role: Role) -> User:
        self._require_admin(caller)
        return self._create(username, password, role, actor_id=caller.id)

    def _create(self, username: str, password: str, role: Role, actor_id: str | None) -> User:
        if not re.fullmatch(USERNAME_PATTERN, username):
            raise BadRequest("username must be 3-64 of A-Z a-z 0-9 . _ @ -", code="bad_username")
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise BadRequest(
                f"password must be at least {MIN_PASSWORD_LENGTH} characters",
                code="weak_password",
            )
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            role=role,
            is_active=True,
            created_at=self.clock.now(),
            password_hash=hash_password(password),
        )
        if not self.repo.add_user(user):
            raise Conflict("username is already taken", code="username_taken")
        self.audit("user_created", actor_id=actor_id, user_id=user.id, role=role.value)
        return user

    def update_user(
        self, caller: Caller, user_id: str, role: Role | None, is_active: bool | None
    ) -> User:
        self._require_admin(caller)
        if user_id == caller.id:
            # Stops an admin locking everyone out by demoting or deactivating themselves.
            raise BadRequest("you cannot change your own role or status", code="self_update")
        if self.repo.get_user(user_id) is None:
            raise NotFound("user")
        self.repo.update_user(user_id, role, is_active)
        if is_active is False or role is not None:
            # Role or status changed: force a fresh sign-in so old sessions can't keep old rights.
            self.repo.delete_user_sessions(user_id)
        self.audit(
            "user_updated",
            actor_id=caller.id,
            user_id=user_id,
            role=role.value if role else None,
            is_active=is_active,
        )
        return self.repo.get_user(user_id)

    def list_users(self, caller: Caller) -> list[User]:
        self._require_admin(caller)
        return self.repo.list_users()

    @staticmethod
    def _require_admin(caller: Caller) -> None:
        if not caller.is_admin:
            raise Forbidden("admin role required", code="admin_required")
