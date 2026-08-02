"""Password hashing for the **dev-only** account store.

Deliberately dependency-free: stdlib PBKDF2-HMAC-SHA256, salted per password, compared in constant
time. These accounts only ever exist when dev login is enabled (never in a graduated schema), so the
bar is "never store plaintext, never leak via timing" — not "resist an offline crack of a production
secret". A mounted-key production deployment has no passwords at all (Google is the only login).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ALGO = "pbkdf2_sha256"
_ROUNDS = 200_000
_SALT_BYTES = 16


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, *, rounds: int = _ROUNDS) -> str:
    """Return an encoded ``pbkdf2_sha256$rounds$salt$hash`` string (salt generated fresh)."""
    salt = os.urandom(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{_ALGO}${rounds}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against an encoded hash. False on any malformed input."""
    try:
        algo, rounds_s, salt_b64, hash_b64 = encoded.split("$")
        if algo != _ALGO:
            return False
        rounds = int(rounds_s)
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except (ValueError, TypeError):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(derived, expected)
