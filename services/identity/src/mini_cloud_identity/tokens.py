"""Mint the platform access token — the single mint path both Google and the dev grant share.

There is exactly one place a token is signed (this module). ``/callback`` (after Google) and
``/dev/token`` (dev password grant) both call :func:`mint_access_token` with the *same* signing key,
``kid``, ``iss``, ``aud`` and TTL, so the resulting token is **byte-for-byte indistinguishable** to
every verifier — the whole reason the dev grant is a faithful test of the real path.
"""

from __future__ import annotations

import time

import jwt

from .keys import SigningKey


def mint_access_token(
    signing: SigningKey,
    *,
    issuer: str,
    audience: str,
    sub: str,
    email: str | None,
    grants: dict[str, str],
    ttl_seconds: int,
    now: int | None = None,
) -> str:
    """Sign a short-lived platform JWT.

    Carries ``sub``/``email`` (identity), the per-app ``grants`` claim (authorization), and the
    standard ``iss``/``aud``/``iat``/``nbf``/``exp`` the SDK's ``require`` list demands. ``now`` is
    injectable for deterministic tests.
    """
    issued = int(time.time()) if now is None else now
    claims: dict[str, object] = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": issued,
        "nbf": issued,
        "exp": issued + ttl_seconds,
        "grants": grants,
    }
    if email:
        claims["email"] = email
    return jwt.encode(
        claims, signing.private_pem, algorithm=signing.algorithm, headers={"kid": signing.kid}
    )
