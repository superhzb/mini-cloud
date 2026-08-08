"""LAN-only developer password login: fail-closed enablement and the seeded admin.

Google OAuth needs a browser, a real account, and client secrets — too heavy for unit tests, CI,
``curl``, and fast local iteration. So the service exposes ``POST /login/password``, which
mints the **same** platform JWT after a username/password check. It exists to exercise the identical
verify path with a token obtained in one HTTP call; only the pre-mint human-check differs.

Security posture is the point of this module. The convenience is real (``admin/admin`` mints a
platform-admin token), so the danger is real too: it must **never** be reachable on a graduated
deployment. We fail closed by refusing non-dev startup and rejecting public Host headers.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

from .passwords import hash_password
from .store import PLATFORM_WIDE_APP, GrantsStore

# Hosts that mark a deployment as local dev. A public hostname (identity.brettbot.ca) is not here.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", ""})


class IdentityConfigError(RuntimeError):
    """A configuration that would be unsafe to run — raised at boot to fail closed, not open."""


@dataclass(frozen=True, slots=True)
class DevAdmin:
    """The default admin seeded when dev login is on: a platform-wide (``"*"``) admin grant."""

    username: str
    password: str
    email: str = "admin@local"
    role: str = "admin"


def _looks_local(issuer: str | None) -> bool:
    """True when ``issuer`` points at this dev box (loopback, ``.local``/``.localhost``, unset)."""
    if not issuer:
        return True  # unset issuer == a fresh local clone
    host = (urlparse(issuer).hostname or "").lower()
    return host in _LOCAL_HOSTS or host.endswith((".local", ".localhost"))


def resolve_dev_login(
    *, enabled: bool, issuer: str | None, app_env: str, force: bool = False
) -> bool:
    """Decide whether the dev password grant may run — **failing closed** on a risky config.

    Returns ``True`` only when the flag is on in ``APP_ENV=dev``. The issuer may be public because
    Google needs an HTTPS callback, but the password endpoint separately rejects non-LAN Host
    headers via :func:`password_request_is_local`. A non-dev deployment still fails closed.
    ``force`` is the deliberate escape hatch for a controlled non-dev test deployment.
    """
    if not enabled:
        return False
    if force:
        return True
    if app_env == "dev":
        return True
    raise IdentityConfigError(
        "MINI_AUTH_PASSWORD_LOGIN is enabled outside local development "
        f"(MINI_AUTH_ISSUER={issuer!r}, APP_ENV={app_env!r}). The dev password grant mints "
        "platform-admin tokens from admin/admin — refusing to boot rather than expose it. "
        "Set MINI_AUTH_PASSWORD_LOGIN=0 on a graduated deployment (or "
        "MINI_AUTH_DEV_LOGIN_FORCE=1 to "
        "override deliberately)."
    )


def password_request_is_local(host: str | None) -> bool:
    """Allow the default developer account only over loopback, a private IP, or mDNS.

    This keeps ``admin/admin`` off a public router/tunnel even when the service uses a public
    Google issuer. The check intentionally uses the HTTP Host selected by the caller: direct LAN
    traffic carries the private IP/mDNS host, while ``identity.example.com`` is rejected.
    """
    candidate = (host or "").strip().lower().rstrip(".")
    if candidate in _LOCAL_HOSTS or candidate.endswith((".local", ".localhost")):
        return True
    try:
        return ipaddress.ip_address(candidate).is_private
    except ValueError:
        return False


def seed_dev_admin(store: GrantsStore, admin: DevAdmin) -> None:
    """Idempotently seed the default admin: a ``dev_users`` row plus a platform-wide grant.

    One ``(email, "*", "admin")`` grant is all it takes to authorize on *every* app out of the box,
    because the SDK's ``require_user`` falls back to the ``"*"`` wildcard (``mini_cloud.auth``).
    """
    store.upsert_dev_user(
        username=admin.username, email=admin.email, password_hash=hash_password(admin.password)
    )
    store.set_grant(email=admin.email, app=PLATFORM_WIDE_APP, role=admin.role)
