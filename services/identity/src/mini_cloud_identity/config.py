"""Service configuration — the identity service's own env, on top of the canonical `mini-cloud`
settings.

The shared knobs (``MINI_AUTH_ISSUER``, ``MINI_AUTH_AUDIENCE``, ``APP_ENV``, ``PORT``) come through
``mini_cloud.config`` so their canonical names stay single-sourced with every verifier. The keys
here are *service-only* — signing-key material, Google client secrets, the dev-login switches — and
are never read by an app (an app holds no secret; it only verifies).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mini_cloud.config import Settings, load_settings

_DEFAULT_PORT = 19210
_DEFAULT_TTL = 900  # 15 min access token (decision 2: short-lived, no session store)


@dataclass(frozen=True, slots=True)
class IdentitySettings:
    """All the identity service needs to boot, mint, and (optionally) run Google + dev login."""

    issuer: str
    audience: str
    port: int
    app_env: str

    # signing key (mounted in prod; ephemeral in dev when absent)
    signing_key_pem: str | None
    signing_kid: str | None
    signing_algorithm: str

    access_ttl: int

    # authorization store (Postgres when set; in-memory dev fallback when not)
    database_url: str | None

    # Google OAuth (absent → the /login flow 503s, but dev login still works)
    google_client_id: str | None
    google_client_secret: str | None
    google_redirect_uri: str | None
    state_secret: str | None
    post_login_redirect: str | None

    # dev login
    dev_login_enabled: bool
    dev_login_force: bool
    dev_admin_user: str
    dev_admin_password: str

    @classmethod
    def from_env(
        cls, environ: dict[str, str] | None = None, *, settings: Settings | None = None
    ) -> IdentitySettings:
        env = dict(os.environ) if environ is None else environ
        base = settings or load_settings(environ=env)

        issuer = base.auth_issuer or f"http://127.0.0.1:{base.port or _DEFAULT_PORT}"

        return cls(
            issuer=issuer,
            audience=base.auth_audience,
            port=base.port or _DEFAULT_PORT,
            app_env=base.app_env,
            signing_key_pem=_read_key(env),
            signing_kid=_opt(env, "MINI_AUTH_KID"),
            signing_algorithm=_opt(env, "MINI_AUTH_ALGORITHM") or "ES256",
            access_ttl=_int(env, "MINI_AUTH_ACCESS_TTL", _DEFAULT_TTL),
            database_url=_opt(env, "IDENTITY_DATABASE_URL"),
            google_client_id=_opt(env, "GOOGLE_CLIENT_ID"),
            google_client_secret=_opt(env, "GOOGLE_CLIENT_SECRET"),
            google_redirect_uri=(
                _opt(env, "GOOGLE_REDIRECT_URI") or f"{issuer.rstrip('/')}/callback"
            ),
            state_secret=_opt(env, "MINI_AUTH_STATE_SECRET"),
            post_login_redirect=_opt(env, "MINI_AUTH_POST_LOGIN_REDIRECT"),
            dev_login_enabled=_bool(env, "MINI_AUTH_DEV_LOGIN", default=True),
            dev_login_force=_bool(env, "MINI_AUTH_DEV_LOGIN_FORCE", default=False),
            dev_admin_user=_opt(env, "MINI_AUTH_DEV_ADMIN_USER") or "admin",
            dev_admin_password=_opt(env, "MINI_AUTH_DEV_ADMIN_PASSWORD") or "admin",
        )


def _opt(env: dict[str, str], key: str) -> str | None:
    value = env.get(key)
    return value if value else None


def _read_key(env: dict[str, str]) -> str | None:
    """Signing key PEM: inline via ``MINI_AUTH_SIGNING_KEY`` or a mounted ``…_FILE`` path."""
    inline = _opt(env, "MINI_AUTH_SIGNING_KEY")
    if inline:
        return inline
    path = _opt(env, "MINI_AUTH_SIGNING_KEY_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8")
    return None


def _int(env: dict[str, str], key: str, default: int) -> int:
    raw = _opt(env, key)
    return int(raw) if raw is not None else default


def _bool(env: dict[str, str], key: str, *, default: bool) -> bool:
    raw = _opt(env, key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
