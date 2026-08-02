"""FastAPI glue: ``require_user`` — the plug-and-play authorization dependency.

Kept out of the core module so the verifier has no hard FastAPI dependency (install
``mini-cloud-auth[fastapi]`` to use this). An app author wires auth in one line::

    from mini_cloud.auth import Principal
    from mini_cloud.auth.fastapi import require_user

    @app.get("/whoami")
    def whoami(user: Principal = Depends(require_user(app="ref-showcase", role="member"))):
        return {"sub": user.sub, "email": user.email}

Status codes follow the authN/authZ split:

- **401** — no/invalid ``Authorization: Bearer`` header, or a token that fails verification
  (bad signature, wrong issuer/audience, expired). Carries ``WWW-Authenticate: Bearer``.
- **403** — a *valid* token whose holder lacks the required ``grants[app]`` (or a high-enough role).
- **503** — the identity service's JWKS can't be fetched (availability fault, not the user's).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from . import (
    JwksUnavailableError,
    Principal,
    TokenInvalidError,
    check_grant,
    default_verifier,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from . import TokenVerifier

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def require_user(
    *,
    app: str | None = None,
    role: str | None = None,
    verifier: TokenVerifier | None = None,
) -> Callable[[Request], Principal]:
    """Build a FastAPI dependency that authenticates the caller and enforces ``grants[app]``.

    Pass ``app`` to require a grant for that app, and ``role`` to require at least that role (see
    :data:`mini_cloud.auth.ROLE_RANK`). With neither, it asserts only "a valid platform identity".
    ``verifier`` overrides the process-default verifier (used in tests to inject an offline one).
    """

    def dependency(request: Request) -> Principal:
        token = _bearer_token(request)
        if token is None:
            raise HTTPException(
                status_code=401, detail="missing bearer token", headers=_BEARER_CHALLENGE
            )
        active = verifier or default_verifier()
        try:
            principal = active.verify_token(token)
        except JwksUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail=f"identity service unavailable: {exc}"
            ) from exc
        except TokenInvalidError as exc:
            raise HTTPException(
                status_code=401, detail=f"invalid token: {exc}", headers=_BEARER_CHALLENGE
            ) from exc
        reason = check_grant(principal, app=app, role=role)
        if reason is not None:
            raise HTTPException(status_code=403, detail=reason)
        return principal

    return dependency


def _bearer_token(request: Request) -> str | None:
    """Extract the bearer token from ``Authorization``, or ``None`` if absent/malformed."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None
