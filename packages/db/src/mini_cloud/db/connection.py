"""Postgres connection helpers — a thin, contract-preserving layer over psycopg 3.

The wire contract is plain Postgres (``DATABASE_URL``); this module adds only ergonomics:
a pool factory, a per-call connection accessor that transparently accepts *either* a live
connection or a pool, and a ``transaction`` context manager. Nothing here hides the fact that
you are talking to Postgres — an app can drop to raw psycopg any time.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import psycopg
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from psycopg import Connection

# A queue/migrator method can be handed a raw connection (caller owns the txn) or a pool
# (we borrow a connection per call). This alias is that union. `type` defers evaluation, so the
# TYPE_CHECKING-only `Connection` reference is fine at runtime.
type ConnSource = Connection | ConnectionPool


def connect(dsn: str, *, autocommit: bool = True) -> Connection:
    """Open a single Postgres connection. Autocommit on by default (the queue manages its own
    transactions per statement; a migration run opens its own explicit transaction)."""
    return psycopg.connect(dsn, autocommit=autocommit)


def make_pool(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    open_now: bool = True,
) -> ConnectionPool[Connection]:
    """Create a psycopg connection pool. Prefer this in a long-running app so requests and the
    job worker share a bounded set of connections rather than opening one each."""
    return ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=open_now)


@contextmanager
def acquire(source: ConnSource) -> Iterator[Connection]:
    """Yield a connection from ``source``.

    If ``source`` is a :class:`ConnectionPool`, borrow one for the duration and return it to the
    pool on exit. If it's already a :class:`~psycopg.Connection`, yield it as-is and leave its
    lifecycle to the caller. This is what lets every queue/migrator method accept either.
    """
    if isinstance(source, ConnectionPool):
        with source.connection() as conn:
            yield conn
    else:
        yield source


@contextmanager
def transaction(source: ConnSource) -> Iterator[Connection]:
    """Yield a connection inside an explicit transaction (commit on success, rollback on error).

    Works whether ``source`` is a pool or a connection. Uses psycopg's native ``transaction()``
    so a nested call becomes a savepoint rather than a second BEGIN.
    """
    with acquire(source) as conn, conn.transaction():
        yield conn
