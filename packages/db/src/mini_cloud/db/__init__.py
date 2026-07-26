"""mini_cloud.db — Postgres connection, migrations, and a job-queue primitive.

Retires the four bespoke SQLite + WAL + single-writer + job-queue stacks
(``fr-hub-api`` ``SqliteWriter``, ``tk-orchestrator``, ``srt-flow/pkg-job-orch``,
``mlx-platform`` records) with one shared, contract-based (plain Postgres) implementation.

    from mini_cloud.config import load_settings
    from mini_cloud.db import make_pool, JobQueue, migrate

    dsn = load_settings().require("database_url")
    pool = make_pool(dsn)
    migrate(pool, "migrations")            # apply the app's own NNNN_*.sql files

    q = JobQueue(pool)
    q.create_schema()
    q.enqueue("emails", {"to": "a@b.c"})
    q.run_worker("emails", send_one)       # at-least-once; handler must be idempotent

See ``queue.py`` for the queue's specified semantics (delivery guarantee, visibility timeout,
retry/backoff, dead-letter) — pinned before ``db`` reaches 1.0 because consumers inherit them.
"""

from __future__ import annotations

from .connection import ConnSource, acquire, connect, make_pool, transaction
from .migrate import Migration, applied_versions, discover, migrate
from .queue import Job, JobQueue, RetryLater, default_backoff

__version__ = "0.1.0"

__all__ = [
    # connection
    "connect",
    "make_pool",
    "acquire",
    "transaction",
    "ConnSource",
    # migrations
    "migrate",
    "discover",
    "applied_versions",
    "Migration",
    # queue
    "JobQueue",
    "Job",
    "RetryLater",
    "default_backoff",
]
