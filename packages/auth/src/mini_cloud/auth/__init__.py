"""mini_cloud.auth — verify a platform identity token and enforce per-app authorization.

This is the *verifier* half of mini-cloud identity (Phase 6). The identity service
(`mini-cloud-identity`, in-repo at `services/identity/`) is the login authority: it bounces a human
through Google OAuth and mints a short-lived, **asymmetrically signed** platform JWT. This package
never talks to Google and holds no secret — it fetches the identity service's public keys from its
JWKS endpoint and validates a token's signature + standard claims locally, so any app can adopt
auth cheaply without a call back to identity on the hot path.

    from mini_cloud.auth import verify_token            # core, FastAPI-free
    principal = verify_token(bearer)                     # -> Principal(sub, email, grants)

    from mini_cloud.auth.fastapi import require_user     # needs mini-cloud-auth[fastapi]

    @app.get("/me")
    def me(user: Principal = Depends(require_user(app="ref-showcase", role="member"))):
        return {"email": user.email, "role": user.role_for("ref-showcase")}

Design (see ``docs/identity-plan.md``):

- **Platform-wide identity, per-app authorization.** One token verifies at every app (fixed
  ``aud: "mini-cloud"``); *who may use which app, at what role* lives in a ``grants`` claim
  (``{app: role}``). ``aud`` proves only "a mini-cloud identity token"; **all** per-app authZ is
  the ``grants[app]`` check — never ``aud`` (decision 4).
- **Asymmetric + JWKS.** ES256/RS256 verified against public keys fetched from
  ``${issuer}/.well-known/jwks.json`` and cached by ``kid`` (refresh on an unknown ``kid`` to ride
  key rotation). No shared secret anywhere.
- **Tiny on purpose.** Depends only on ``config`` + PyJWT + httpx — no ``db``/``storage`` — so it
  stays a cheap drop-in for any demo. The whole storage-backed grants story lives in the identity
  service; here we only ever see the signed JWT.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import httpx
import jwt

if TYPE_CHECKING:
    from mini_cloud.config import Settings

__version__ = "0.1.0"

__all__ = [
    "Principal",
    "AuthConfig",
    "TokenVerifier",
    "verify_token",
    "from_settings",
    "configure",
    "default_verifier",
    "check_grant",
    "ROLE_RANK",
    "WILDCARD_APP",
    "AuthError",
    "TokenInvalidError",
    "JwksUnavailableError",
    "DEFAULT_AUDIENCE",
    "DEFAULT_ALGORITHMS",
]

DEFAULT_AUDIENCE = "mini-cloud"
DEFAULT_ALGORITHMS: tuple[str, ...] = ("ES256", "RS256")

# A resolved JWKS verification key. PyJWT hands back a cryptography public-key object whose concrete
# type varies by algorithm (EC/RSA/…); `jwt.decode` accepts it as-is, so we keep it opaque.
_VerifyKey = Any

# Coarse role hierarchy: a higher-ranked grant satisfies a lower-ranked requirement (an `admin`
# passes `role="member"`). Roles outside this map (an app's bespoke role) require an *exact* match —
# we never silently rank a name we don't understand. Apps that want their own ladder pass
# `role=None` and read `principal.role_for(app)` themselves.
ROLE_RANK: dict[str, int] = {"viewer": 0, "member": 1, "admin": 2}

# A grant keyed by this pseudo-app authorizes *every* app: a platform-wide grant. It's how one
# `("email", "*", "admin")` row (the dev default admin) authorizes everywhere without a row per app.
# An explicit per-app grant always wins over the wildcard (see `Principal.role_for`), so a wildcard
# admin can still be scoped *down* for a specific app by granting a narrower role there.
WILDCARD_APP = "*"


class AuthError(RuntimeError):
    """Base class for auth failures raised by this package."""


class TokenInvalidError(AuthError):
    """The token is missing, malformed, expired, or fails signature/claim validation (→ 401)."""


class JwksUnavailableError(AuthError):
    """The identity service's JWKS could not be fetched — a config/availability fault, not a bad
    token (→ 503). Distinguished from :class:`TokenInvalidError` so a down identity service reads
    as "try again", not "you're unauthenticated"."""


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated mini-cloud human, as carried by a verified platform JWT.

    ``grants`` is the per-app authorization map (``{app_name: role}``) folded in at mint time from
    the identity service's ``grants`` table. An authenticated user with **no** grants is normal —
    it just means they may enter no app yet (every ``require_user(app=…)`` then 403s).
    """

    sub: str
    email: str | None
    grants: dict[str, str] = field(default_factory=dict)
    claims: dict[str, Any] = field(default_factory=dict)

    def role_for(self, app: str) -> str | None:
        """This principal's **effective** role in ``app``.

        Their explicit ``grants[app]`` if present; else a platform-wide ``grants["*"]``
        (:data:`WILDCARD_APP`) if they hold one; else ``None``. Resolving the wildcard here keeps
        introspection in lockstep with authorization — :func:`check_grant` reads the same value, so
        a ``"*"``-only platform admin both *passes* ``require_user(app=…)`` and *reports* their role
        for that app, rather than authorizing while ``role_for`` returns ``None``.
        """
        role = self.grants.get(app)
        return role if role is not None else self.grants.get(WILDCARD_APP)

    def is_authorized(self, app: str, role: str | None = None) -> bool:
        """True if this principal may use ``app`` (at ``role`` or higher, per :data:`ROLE_RANK`)."""
        return check_grant(self, app=app, role=role) is None


def check_grant(principal: Principal, *, app: str | None, role: str | None) -> str | None:
    """Authorization gate. Returns ``None`` when authorized, else a human reason (caller → 403).

    ``app=None`` means "any authenticated user" (authN only). Otherwise the principal must hold a
    grant for ``app`` — an explicit ``grants[app]`` or a platform-wide ``grants["*"]``
    (:data:`WILDCARD_APP`, resolved by :meth:`Principal.role_for`); if ``role`` is given, that grant
    must satisfy it (see :data:`ROLE_RANK`).
    """
    if app is None:
        return None
    have = principal.role_for(app)  # resolves the "*" platform-wide fallback
    if have is None:
        return f"no grant for app '{app}'"
    if role is None:
        return None
    if _role_satisfies(have, role):
        return None
    return f"role '{have}' for app '{app}' does not satisfy required role '{role}'"


def _role_satisfies(have: str, need: str) -> bool:
    if have == need:
        return True
    have_rank, need_rank = ROLE_RANK.get(have), ROLE_RANK.get(need)
    if have_rank is None or need_rank is None:
        return False  # an unknown role on either side must match exactly
    return have_rank >= need_rank


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Everything a verifier needs to validate a token, independent of *where* keys come from."""

    issuer: str
    audience: str = DEFAULT_AUDIENCE
    jwks_url: str | None = None
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    leeway: float = 30.0  # seconds of clock-skew tolerance on exp/nbf/iat

    def resolved_jwks_url(self) -> str:
        """The JWKS URL, defaulting to ``${issuer}/.well-known/jwks.json`` (decision + config)."""
        return self.jwks_url or self.issuer.rstrip("/") + "/.well-known/jwks.json"


class _KeyResolver(Protocol):
    """Resolves a token's ``kid`` to the public key that should verify it."""

    def resolve(self, token: str) -> _VerifyKey: ...


class _StaticJwksResolver:
    """A fixed in-memory JWKS — the offline/test path, and a way to pin keys with no network.

    Same ``kid``-lookup semantics as the network resolver, so a test exercises the real matching
    logic; it just never fetches.
    """

    def __init__(self, jwks: dict[str, Any] | jwt.PyJWKSet) -> None:
        self._jwks = jwks if isinstance(jwks, jwt.PyJWKSet) else jwt.PyJWKSet.from_dict(jwks)

    def resolve(self, token: str) -> _VerifyKey:
        kid = _kid(token)
        key = _match(self._jwks, kid)
        if key is None:
            raise TokenInvalidError(f"no signing key for kid={kid!r} in the configured JWKS")
        return key


class _HttpxJwksResolver:
    """Fetches + caches the identity service's JWKS over httpx, keyed by ``kid``.

    Caches the whole key set and, on a token whose ``kid`` isn't in the cache, refetches once (a
    key was rotated in) — rate-limited so a flood of bogus ``kid``s can't turn into a fetch storm.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        client: httpx.Client | None = None,
        min_refresh_interval: float = 10.0,
    ) -> None:
        self._url = jwks_url
        self._client = client or httpx.Client(timeout=10.0)
        self._min_refresh_interval = min_refresh_interval
        self._jwks: jwt.PyJWKSet | None = None
        self._last_fetch = 0.0
        self._lock = threading.Lock()

    def resolve(self, token: str) -> _VerifyKey:
        kid = _kid(token)
        if self._jwks is None:
            self._fetch()
        key = _match(self._jwks, kid)
        if key is None and time.monotonic() - self._last_fetch >= self._min_refresh_interval:
            self._fetch()  # unknown kid → maybe a rotation; refetch and retry once
            key = _match(self._jwks, kid)
        if key is None:
            raise TokenInvalidError(f"no signing key for kid={kid!r} in JWKS at {self._url}")
        return key

    def _fetch(self) -> None:
        with self._lock:
            try:
                resp = self._client.get(self._url)
                resp.raise_for_status()
                self._jwks = jwt.PyJWKSet.from_dict(resp.json())
            except (httpx.HTTPError, jwt.PyJWTError, ValueError) as exc:
                # No usable keys and we can't fetch → identity is unreachable/misconfigured.
                if self._jwks is None:
                    raise JwksUnavailableError(
                        f"could not load JWKS from {self._url}: {exc}"
                    ) from exc
            finally:
                self._last_fetch = time.monotonic()


def _kid(token: str) -> str | None:
    try:
        return jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise TokenInvalidError(f"malformed token header: {exc}") from exc


def _match(jwks: jwt.PyJWKSet | None, kid: str | None) -> _VerifyKey:
    if jwks is None:
        return None
    for key in jwks.keys:
        if kid is None or key.key_id == kid:
            return key.key
    return None


class TokenVerifier:
    """Validates platform JWTs against a resolver's public keys.

    Build one of these once per process and share it (it caches JWKS). The common paths are the
    classmethods: :meth:`from_settings` / :meth:`from_config` for the real network verifier, and
    :meth:`from_jwks_set` for offline tests or key pinning.
    """

    def __init__(self, config: AuthConfig, *, resolver: _KeyResolver) -> None:
        self._config = config
        self._resolver = resolver

    @property
    def config(self) -> AuthConfig:
        return self._config

    @classmethod
    def from_config(
        cls, config: AuthConfig, *, client: httpx.Client | None = None
    ) -> TokenVerifier:
        return cls(config, resolver=_HttpxJwksResolver(config.resolved_jwks_url(), client=client))

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> TokenVerifier:
        """Build the network verifier from canonical config (``MINI_AUTH_*``)."""
        if settings is None:
            from mini_cloud.config import load_settings

            settings = load_settings()
        config = AuthConfig(
            issuer=settings.require("auth_issuer"),
            audience=settings.auth_audience or DEFAULT_AUDIENCE,
            jwks_url=settings.auth_jwks_url,
        )
        return cls.from_config(config)

    @classmethod
    def from_jwks_set(
        cls,
        jwks: dict[str, Any] | jwt.PyJWKSet,
        *,
        issuer: str,
        audience: str = DEFAULT_AUDIENCE,
        algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS,
        leeway: float = 30.0,
    ) -> TokenVerifier:
        """An offline verifier over a fixed JWKS — used by tests and for pinning keys."""
        config = AuthConfig(
            issuer=issuer, audience=audience, algorithms=tuple(algorithms), leeway=leeway
        )
        return cls(config, resolver=_StaticJwksResolver(jwks))

    def verify_token(self, token: str) -> Principal:
        """Verify ``token`` and return its :class:`Principal`.

        Validates the signature against the JWKS key for the token's ``kid``, and requires
        ``iss``/``aud``/``exp``/``sub``. Raises :class:`TokenInvalidError` on any failure (bad
        signature, wrong issuer/audience, expired, missing claim) and :class:`JwksUnavailableError`
        if the key set can't be fetched.
        """
        if not token:
            raise TokenInvalidError("empty token")
        key = self._resolver.resolve(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self._config.algorithms),
                audience=self._config.audience,
                issuer=self._config.issuer,
                leeway=self._config.leeway,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenInvalidError(str(exc)) from exc
        return _principal_from_claims(claims)


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    raw = claims.get("grants")
    grants = (
        {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    )  # a missing/odd grants claim → no grants (authenticated, unauthorized everywhere)
    email = claims.get("email")
    return Principal(
        sub=str(claims["sub"]),
        email=str(email) if email is not None else None,
        grants=grants,
        claims=claims,
    )


# --- process-wide default verifier -------------------------------------------------------
# So an app author writes `require_user(app=…)` with no plumbing: the first check lazily builds the
# verifier from env. Tests (and apps that want explicit wiring) call `configure()` to inject one.
_default: TokenVerifier | None = None
_default_lock = threading.Lock()


def configure(verifier: TokenVerifier | None) -> None:
    """Install (or clear, with ``None``) the process-wide default verifier."""
    global _default
    with _default_lock:
        _default = verifier


def default_verifier() -> TokenVerifier:
    """The process-wide verifier, built lazily from ``MINI_AUTH_*`` env on first use."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = TokenVerifier.from_settings()
    return _default


def from_settings(settings: Settings | None = None) -> TokenVerifier:
    """Build a :class:`TokenVerifier` from canonical config (convenience re-export)."""
    return TokenVerifier.from_settings(settings)


def verify_token(token: str, *, verifier: TokenVerifier | None = None) -> Principal:
    """Verify ``token`` with the given (or process-default) verifier. See
    :meth:`TokenVerifier.verify_token`."""
    return (verifier or default_verifier()).verify_token(token)
