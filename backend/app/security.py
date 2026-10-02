"""Password hashing, session/CSRF tokens, and login throttling primitives.

No framework for any of this — argon2-cffi for hashing, stdlib ``secrets`` and
``hashlib`` for tokens, ``hmac.compare_digest`` for constant-time comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

MAX_FAILED_LOGINS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

SESSION_IDLE_TIMEOUT = timedelta(minutes=30)
SESSION_ABSOLUTE_TIMEOUT = timedelta(hours=12)

LOGIN_RATE_LIMIT_WINDOW = timedelta(minutes=15)
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 30  # per source IP, across all accounts


class WeakPasswordError(ValueError):
    pass


def validate_password_policy(password: str) -> None:
    """Length-based policy (NIST 800-63B): length matters more than forced
    complexity rules, which push users toward predictable substitutions."""
    if not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise WeakPasswordError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters"
        )


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_session_token() -> tuple[str, str]:
    """Returns (raw_token_for_cookie, hash_to_persist).

    The session token is the sole secret bound to a browser (httponly cookie),
    so only its hash is persisted — DB exposure alone must not let an attacker
    forge a session.
    """
    raw = _new_token()
    return raw, _sha256_hex(raw)


def generate_csrf_token() -> str:
    """CSRF tokens are designed to be read by client JS and echoed back, so
    (unlike the session token) there's no benefit to hashing it at rest —
    it's stored raw so ``/api/auth/me`` can hand it back out on request."""
    return _new_token()


def hash_token(raw: str) -> str:
    return _sha256_hex(raw)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def utcnow() -> datetime:
    return datetime.now(UTC)
