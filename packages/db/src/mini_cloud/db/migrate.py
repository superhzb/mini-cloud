"""A deliberately small SQL migration runner.

Not an ORM and not Alembic — apps that adopt this get plain, ordered ``.sql`` files and a record
of what ran. That is enough for prototype-factory apps and keeps the wire contract (Postgres)
front and centre. Files are named ``NNNN_description.sql`` (e.g. ``0001_init.sql``); they run in
lexical order, each in its own transaction, and each is recorded in ``mini_cloud_migrations`` so a
second run is a no-op.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .connection import ConnSource, acquire, transaction

_MIGRATIONS_TABLE = "mini_cloud_migrations"
_FILENAME_RE = re.compile(r"^(\d+)[_-].*\.sql$")

_ENSURE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {_MIGRATIONS_TABLE} (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True, slots=True)
class Migration:
    """One migration file: its sort key ``version`` (the numeric prefix), name, and SQL body."""

    version: str
    name: str
    sql: str


def discover(migrations_dir: str | Path) -> list[Migration]:
    """Load and sort ``NNNN_*.sql`` files from a directory. Non-matching files are ignored so a
    ``README.md`` can live alongside them; a duplicate numeric prefix is an error (ambiguous
    order)."""
    d = Path(migrations_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"migrations dir not found: {d}")
    found: dict[str, Migration] = {}
    for path in sorted(d.iterdir()):
        m = _FILENAME_RE.match(path.name)
        if not m:
            continue
        version = m.group(1)
        if version in found:
            raise ValueError(
                f"duplicate migration version {version!r}: {found[version].name} and {path.name}"
            )
        found[version] = Migration(version=version, name=path.name, sql=path.read_text("utf-8"))
    return [found[v] for v in sorted(found, key=int)]


def applied_versions(source: ConnSource) -> set[str]:
    """Return the set of already-applied migration versions (creating the ledger table if new)."""
    with acquire(source) as conn:
        conn.execute(_ENSURE_TABLE)
        rows = conn.execute(f"SELECT version FROM {_MIGRATIONS_TABLE}").fetchall()
    return {r[0] for r in rows}


def migrate(source: ConnSource, migrations_dir: str | Path) -> list[str]:
    """Apply every pending migration from ``migrations_dir`` in order.

    Each file runs in its own transaction together with the ledger insert, so a crash leaves the
    ledger consistent with what actually ran. Returns the list of versions applied this call
    (empty if already up to date). Idempotent.
    """
    with acquire(source) as conn:
        conn.execute(_ENSURE_TABLE)
    done = applied_versions(source)
    newly: list[str] = []
    for mig in discover(migrations_dir):
        if mig.version in done:
            continue
        with transaction(source) as conn:
            conn.execute(mig.sql)  # type: ignore[arg-type]  # trusted local .sql file
            conn.execute(
                f"INSERT INTO {_MIGRATIONS_TABLE} (version) VALUES (%s)",
                (mig.version,),
            )
        newly.append(mig.version)
    return newly
