"""The dev-only password grant: fail-closed enablement, the seeded admin, and the ``dev_users`` DDL.

Google OAuth needs a browser, a real account, and client secrets — too heavy for unit tests, CI,
``curl``, and fast local iteration. So the service *optionally* exposes ``POST /dev/token``, which
mints the **same** platform JWT after a username/password check. It exists to exercise the identical
verify path with a token obtained in one HTTP call; only the pre-mint human-check differs.

Security posture is the point of this module. The convenience is real (``admin/admin`` mints a
platform-admin token), so the danger is real too: it must **never** be reachable on a graduated
deployment. Rather than trusting an operator to remember ``MINI_AUTH_DEV_LOGIN=0``, we **fail
closed** — :func:`resolve_dev_login` refuses to boot the service if the flag is on but the
deployment doesn't look like local dev. Forgetting the flag turns into a startup crash with a clear
message, not a silent platform-wide backdoor.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .passwords import hash_password
from .store import PLATFORM_WIDE_APP, GrantsStore

# Hosts that mark a deployment as local dev. A public hostname (identity.brettbot.ca) is not here.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", ""})

# `dev_users` is created only when dev login is enabled, so it never ships in a graduated schema.
DEV_USERS_DDL = """
CREATE TABLE IF NOT EXISTS dev_users (
    username      TEXT        PRIMARY KEY,
    email         TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


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

    Returns ``True`` only when the flag is on *and* the deployment looks like local dev (``app_env
    == "dev"`` and a loopback/``.local`` issuer). If the flag is on but the deployment looks
    remote/graduated, raise :class:`IdentityConfigError` so the service refuses to boot rather than
    exposing ``admin/admin`` to the world. ``force`` (``MINI_AUTH_DEV_LOGIN_FORCE=1``) is a
    deliberate, documented override for the rare "public host but truly a throwaway" case.
    """
    if not enabled:
        return False
    if force:
        return True
    if app_env == "dev" and _looks_local(issuer):
        return True
    raise IdentityConfigError(
        "MINI_AUTH_DEV_LOGIN is enabled but this does not look like local dev "
        f"(MINI_AUTH_ISSUER={issuer!r}, APP_ENV={app_env!r}). The dev password grant mints "
        "platform-admin tokens from admin/admin — refusing to boot rather than expose it. "
        "Set MINI_AUTH_DEV_LOGIN=0 on a graduated deployment (or MINI_AUTH_DEV_LOGIN_FORCE=1 to "
        "override deliberately)."
    )


def seed_dev_admin(store: GrantsStore, admin: DevAdmin) -> None:
    """Idempotently seed the default admin: a ``dev_users`` row plus a platform-wide grant.

    One ``(email, "*", "admin")`` grant is all it takes to authorize on *every* app out of the box,
    because the SDK's ``require_user`` falls back to the ``"*"`` wildcard (``mini_cloud.auth``).
    """
    store.upsert_dev_user(
        username=admin.username, email=admin.email, password_hash=hash_password(admin.password)
    )
    store.set_grant(email=admin.email, app=PLATFORM_WIDE_APP, role=admin.role)
