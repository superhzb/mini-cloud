"""The authorization store: ``users`` (profile cache) + ``grants`` (per-app roles) + the developer
``dev_users`` (username/password) table.

Two implementations behind one :class:`GrantsStore` protocol:

- :class:`PostgresStore` — the real store, over the provisioned ``identity`` database. The service
  is its sole writer and applies its own migrations on boot (see :mod:`.migrations`).
- :class:`InMemoryStore` — no Postgres. Used by the unit tests (this repo runs pytest per-package
  without Docker) and as a zero-setup fallback for a fresh clone, with a loud "ephemeral" warning.

Both are storage-agnostic to the caller: the mint path only ever asks "what are this email's
grants?" and upserts a profile — it never sees SQL. That's what keeps the *token contract* the only
thing an app depends on (``docs/identity-plan.md`` → "Where per-app grants live").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mini_cloud.auth import WILDCARD_APP

if TYPE_CHECKING:
    from mini_cloud.db import ConnSource


@dataclass(frozen=True, slots=True)
class DevUser:
    """A developer login account.

    Maps a username to an ``email`` whose grants become the token's.
    """

    username: str
    email: str
    password_hash: str


class GrantsStore(Protocol):
    """Everything the mint path and the dev-login seed need from the authorization store."""

    def grants_for(self, email: str) -> dict[str, str]:
        """This email's ``{app: role}`` map (empty → authenticated but authorized for no app)."""
        ...

    def upsert_user(
        self, *, sub: str, email: str | None, name: str | None, picture: str | None
    ) -> None:
        """Cache a Google profile (idempotent on ``sub``). Never a source of authZ — grants are."""
        ...

    def set_grant(self, *, email: str, app: str, role: str) -> None:
        """Grant ``email`` ``role`` on ``app`` (upsert on ``(email, app)``). ``app="*"`` is
        platform-wide (see :data:`mini_cloud.auth.WILDCARD_APP`)."""
        ...

    def get_dev_user(self, username: str) -> DevUser | None:
        """Look up a dev-login account, or ``None``. Only populated when dev login is enabled."""
        ...

    def upsert_dev_user(self, *, username: str, email: str, password_hash: str) -> None:
        """Create/replace a dev-login account (idempotent on ``username``)."""
        ...


class InMemoryStore:
    """A process-local store — no database. Exercises the exact same mint path as Postgres."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], str] = {}
        self._users: dict[str, dict[str, str | None]] = {}
        self._dev_users: dict[str, DevUser] = {}

    def grants_for(self, email: str) -> dict[str, str]:
        return {app: role for (e, app), role in self._grants.items() if e == email}

    def upsert_user(
        self, *, sub: str, email: str | None, name: str | None, picture: str | None
    ) -> None:
        self._users[sub] = {"email": email, "name": name, "picture": picture}

    def set_grant(self, *, email: str, app: str, role: str) -> None:
        self._grants[(email, app)] = role

    def get_dev_user(self, username: str) -> DevUser | None:
        return self._dev_users.get(username)

    def upsert_dev_user(self, *, username: str, email: str, password_hash: str) -> None:
        self._dev_users[username] = DevUser(
            username=username, email=email, password_hash=password_hash
        )


class PostgresStore:
    """The real store over the provisioned ``identity`` database.

    Accepts any :class:`~mini_cloud.db.ConnSource` (a pool in the running service; a bare connection
    in a test). Wildcard grants are ordinary rows with ``app = '*'`` — no special-casing here; the
    ``"*"`` fallback is the SDK verifier's job.
    """

    def __init__(self, source: ConnSource) -> None:
        self._source = source

    def grants_for(self, email: str) -> dict[str, str]:
        from mini_cloud.db import acquire

        with acquire(self._source) as conn:
            rows = conn.execute(
                "SELECT app, role FROM grants WHERE email = %s", (email,)
            ).fetchall()
        return {app: role for app, role in rows}

    def upsert_user(
        self, *, sub: str, email: str | None, name: str | None, picture: str | None
    ) -> None:
        from mini_cloud.db import transaction

        with transaction(self._source) as conn:
            conn.execute(
                """
                INSERT INTO users (sub, email, name, picture, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (sub) DO UPDATE
                  SET email = EXCLUDED.email, name = EXCLUDED.name,
                      picture = EXCLUDED.picture, updated_at = now()
                """,
                (sub, email, name, picture),
            )

    def set_grant(self, *, email: str, app: str, role: str) -> None:
        from mini_cloud.db import transaction

        with transaction(self._source) as conn:
            conn.execute(
                """
                INSERT INTO grants (email, app, role, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (email, app) DO UPDATE
                  SET role = EXCLUDED.role, updated_at = now()
                """,
                (email, app, role),
            )

    def get_dev_user(self, username: str) -> DevUser | None:
        from mini_cloud.db import acquire

        with acquire(self._source) as conn:
            row = conn.execute(
                "SELECT username, email, password_hash FROM dev_users WHERE username = %s",
                (username,),
            ).fetchone()
        if row is None:
            return None
        return DevUser(username=row[0], email=row[1], password_hash=row[2])

    def upsert_dev_user(self, *, username: str, email: str, password_hash: str) -> None:
        from mini_cloud.db import transaction

        with transaction(self._source) as conn:
            conn.execute(
                """
                INSERT INTO dev_users (username, email, password_hash)
                VALUES (%s, %s, %s)
                ON CONFLICT (username) DO UPDATE
                  SET email = EXCLUDED.email, password_hash = EXCLUDED.password_hash
                """,
                (username, email, password_hash),
            )


# Re-exported so callers can spell the platform-wide app without importing from the SDK directly.
PLATFORM_WIDE_APP = WILDCARD_APP
