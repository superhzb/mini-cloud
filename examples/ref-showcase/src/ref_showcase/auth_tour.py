"""The identity tour: an inspectable view of how this app wires platform auth.

``mini-cloud-auth`` is a tiny, opt-in SDK — the only per-request surface is the ``require_user``
FastAPI dependency (wired on the protected route in ``app.py``). This module backs the *unprotected*
``/auth/config`` endpoint so the console (and a curious reader) can see whether auth is switched on
and what trust anchor it points at, without needing a token. None of these values are secret — the
verifier holds no key material; it only ever fetches *public* keys from the issuer's JWKS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mini_cloud.auth import DEFAULT_AUDIENCE, ROLE_RANK, AuthConfig

if TYPE_CHECKING:
    from mini_cloud.config import Settings

# The one protected app the tour guards. Kept here so the endpoint decorator and the docs agree.
SHOWCASE_APP = "ref-showcase"
SHOWCASE_ROLE = "member"


def auth_snapshot(settings: Settings) -> dict[str, object]:
    """Report whether identity is configured and the trust anchor it resolves to.

    Returns ``configured: False`` (rather than raising) when ``MINI_AUTH_ISSUER`` is unset, so the
    console can render "auth off" the same way it renders a missing inference gateway.
    """
    issuer = settings.auth_issuer
    if not issuer:
        return {
            "configured": False,
            "detail": "identity disabled — set MINI_AUTH_ISSUER to enable auth",
            "audience": settings.auth_audience or DEFAULT_AUDIENCE,
            "roles": _role_ladder(),
            "guarded": {"app": SHOWCASE_APP, "min_role": SHOWCASE_ROLE},
        }
    config = AuthConfig(
        issuer=issuer,
        audience=settings.auth_audience or DEFAULT_AUDIENCE,
        jwks_url=settings.auth_jwks_url,
    )
    return {
        "configured": True,
        "issuer": config.issuer,
        "jwks_url": config.resolved_jwks_url(),
        "audience": config.audience,
        "roles": _role_ladder(),
        "guarded": {"app": SHOWCASE_APP, "min_role": SHOWCASE_ROLE},
    }


def _role_ladder() -> list[str]:
    """The coarse role hierarchy, weakest first (``viewer`` < ``member`` < ``admin``)."""
    return [name for name, _rank in sorted(ROLE_RANK.items(), key=lambda item: item[1])]
