"""A Postgres job queue — the SDK primitive that retires the four bespoke SQLite/job stacks.

## Semantics (specified before ``db`` is pinned 1.0, because consumers inherit them)

**Delivery guarantee — at-least-once.** ``dequeue`` reserves a job with
``SELECT … FOR UPDATE SKIP LOCKED`` and stamps it with a *visibility deadline* (``vt``). A worker
that crashes without ack/nack simply lets ``vt`` lapse, after which the job is redelivered.
Because a job can therefore run more than once, **handlers must be idempotent** — enqueue with a
``dedupe_key`` and/or make the side effect safe to repeat. There is no exactly-once.

**Visibility timeout.** On dequeue, ``vt`` is set to ``now() + visibility_timeout``. Until then no
other worker can pick the job up. A long handler must call :meth:`JobQueue.extend` to push the
deadline out (a heartbeat) or risk a second worker starting the same job.

**Retry / backoff.** Each dequeue increments ``attempts``. On failure the worker calls
:meth:`JobQueue.nack`, which reschedules the job at ``now() + backoff(attempts)`` (exponential by
default). Delivery N is thus separated from N+1 by growing delay.

**Dead-letter.** When ``attempts >= max_attempts`` a failed job is moved to
``mini_cloud_dead_letter`` (kept, not deleted) with its last error, so poison messages leave the
hot path but remain inspectable. Once the underlying cause is fixed an operator replays them with
:meth:`JobQueue.requeue_dead_letter`, which moves them back onto the live queue with a reset
attempt count — the manual counterpart to the automatic dead-lettering in :meth:`JobQueue.nack`.

**Ordering.** Best-effort priority-then-FIFO (``ORDER BY priority DESC, vt, id``); ``SKIP LOCKED``
means strict global ordering is not guaranteed under concurrency — by design, for throughput.

This is a single-table design (one row per job, many named queues via the ``queue`` column),
chosen over per-queue tables for simplicity at prototype-factory scale. It follows the pgmq
pattern rather than blessing a fifth hand-rolled queue.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from .connection import ConnSource, acquire

_log = logging.getLogger("mini_cloud.db.queue")

QUEUE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mini_cloud_jobs (
    id           BIGSERIAL PRIMARY KEY,
    queue        TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    priority     INT         NOT NULL DEFAULT 0,
    attempts     INT         NOT NULL DEFAULT 0,
    max_attempts INT         NOT NULL DEFAULT 5,
    dedupe_key   TEXT,
    vt           TIMESTAMPTZ NOT NULL DEFAULT now(),   -- visible when vt <= now()
    locked_by    TEXT,
    locked_at    TIMESTAMPTZ,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot path: find the next visible job in a queue. Partial-free composite index on (queue, vt).
CREATE INDEX IF NOT EXISTS mini_cloud_jobs_ready_idx
    ON mini_cloud_jobs (queue, priority DESC, vt, id);

-- Idempotent enqueue: at most one live job per (queue, dedupe_key) when a key is supplied.
CREATE UNIQUE INDEX IF NOT EXISTS mini_cloud_jobs_dedupe_idx
    ON mini_cloud_jobs (queue, dedupe_key) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS mini_cloud_dead_letter (
    id           BIGINT      PRIMARY KEY,
    queue        TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    attempts     INT         NOT NULL,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL,
    died_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_SELECT_COLS = "id, queue, payload, priority, attempts, max_attempts, dedupe_key, created_at"


def default_backoff(
    attempts: int, *, base_seconds: float = 5.0, cap_seconds: float = 3600.0
) -> float:
    """Exponential backoff: ``base * 2**(attempts-1)``, capped. attempts is 1-based (first
    delivery is attempt 1, so its retry waits ``base`` seconds)."""
    exp = max(0, attempts - 1)
    return min(cap_seconds, base_seconds * (2.0**exp))


@dataclass(slots=True)
class Job:
    """A reserved unit of work. Hold it only for the duration of the visibility timeout; ack or
    nack it before ``vt`` lapses (or :meth:`JobQueue.extend` it)."""

    id: int
    queue: str
    payload: dict[str, Any]
    priority: int
    attempts: int
    max_attempts: int
    dedupe_key: str | None
    created_at: datetime

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts)


class RetryLater(Exception):  # noqa: N818 — a control-flow signal, not an error condition
    """Raise from a worker handler to nack with an explicit delay instead of failing hard."""

    def __init__(self, delay_seconds: float | None = None) -> None:
        super().__init__(f"retry in {delay_seconds}s" if delay_seconds else "retry")
        self.delay_seconds = delay_seconds


class JobQueue:
    """A job queue bound to a connection source (a pool in production, a connection in tests).

    All methods borrow a connection from the source per call, so a single ``JobQueue`` is safe to
    share across an app's request handlers and its background worker.
    """

    def __init__(self, source: ConnSource) -> None:
        self._source = source

    # --- schema ---------------------------------------------------------------------
    def create_schema(self) -> None:
        """Create the queue tables/indexes if absent. Idempotent; safe to call at boot."""
        with acquire(self._source) as conn:
            conn.execute(QUEUE_SCHEMA_SQL)
            if not conn.autocommit:
                conn.commit()

    # --- producer -------------------------------------------------------------------
    def enqueue(
        self,
        queue: str,
        payload: Mapping[str, Any],
        *,
        delay_seconds: float = 0.0,
        priority: int = 0,
        max_attempts: int = 5,
        dedupe_key: str | None = None,
    ) -> int | None:
        """Insert a job. Returns its id, or ``None`` if a ``dedupe_key`` collided with a live job
        (idempotent enqueue: the existing job stands, nothing new is created)."""
        sql = """
            INSERT INTO mini_cloud_jobs (queue, payload, priority, max_attempts, dedupe_key, vt)
            VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))
            ON CONFLICT (queue, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            RETURNING id
        """
        with acquire(self._source) as conn:
            row = conn.execute(
                sql,
                (queue, Jsonb(dict(payload)), priority, max_attempts, dedupe_key, delay_seconds),
            ).fetchone()
            if not conn.autocommit:
                conn.commit()
        return int(row[0]) if row else None

    # --- consumer -------------------------------------------------------------------
    def dequeue(
        self,
        queue: str,
        *,
        visibility_timeout: float = 30.0,
        worker_id: str | None = None,
    ) -> Job | None:
        """Atomically reserve the next visible job in ``queue`` (or ``None`` if none is ready).

        Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never block each other and never
        hand the same job to two workers within the visibility window.
        """
        sql = f"""
            UPDATE mini_cloud_jobs SET
                vt        = now() + make_interval(secs => %s),
                attempts  = attempts + 1,
                locked_by = %s,
                locked_at = now()
            WHERE id = (
                SELECT id FROM mini_cloud_jobs
                WHERE queue = %s AND vt <= now()
                ORDER BY priority DESC, vt, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING {_SELECT_COLS}
        """
        with acquire(self._source) as conn:
            row = conn.execute(sql, (visibility_timeout, worker_id, queue)).fetchone()
            if not conn.autocommit:
                conn.commit()
        return _row_to_job(row) if row else None

    def ack(self, job: Job) -> None:
        """Mark a job done by deleting it. Call only after the side effect is durable."""
        with acquire(self._source) as conn:
            conn.execute("DELETE FROM mini_cloud_jobs WHERE id = %s", (job.id,))
            if not conn.autocommit:
                conn.commit()

    def nack(
        self, job: Job, *, delay_seconds: float | None = None, error: str | None = None
    ) -> bool:
        """Fail the current delivery. Reschedules with backoff, or dead-letters if attempts are
        exhausted. Returns ``True`` if rescheduled, ``False`` if it was dead-lettered."""
        if job.attempts >= job.max_attempts:
            self._to_dead_letter(job, error)
            return False
        delay = default_backoff(job.attempts) if delay_seconds is None else delay_seconds
        with acquire(self._source) as conn:
            conn.execute(
                "UPDATE mini_cloud_jobs SET vt = now() + make_interval(secs => %s), "
                "locked_by = NULL, locked_at = NULL, last_error = %s WHERE id = %s",
                (delay, error, job.id),
            )
            if not conn.autocommit:
                conn.commit()
        return True

    def extend(self, job: Job, *, seconds: float) -> None:
        """Push a reserved job's visibility deadline out by ``seconds`` from now (heartbeat)."""
        with acquire(self._source) as conn:
            conn.execute(
                "UPDATE mini_cloud_jobs SET vt = now() + make_interval(secs => %s) WHERE id = %s",
                (seconds, job.id),
            )
            if not conn.autocommit:
                conn.commit()

    def _to_dead_letter(self, job: Job, error: str | None) -> None:
        with acquire(self._source) as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO mini_cloud_dead_letter "
                    "(id, queue, payload, attempts, last_error, created_at) "
                    "SELECT id, queue, payload, attempts, %s, created_at "
                    "FROM mini_cloud_jobs WHERE id = %s "
                    "ON CONFLICT (id) DO NOTHING",
                    (error, job.id),
                )
                conn.execute("DELETE FROM mini_cloud_jobs WHERE id = %s", (job.id,))

    # --- introspection --------------------------------------------------------------
    def depth(self, queue: str) -> dict[str, int]:
        """Return ``{"ready": n, "in_flight": m, "total": n+m}`` for a queue."""
        with acquire(self._source) as conn:
            row = conn.execute(
                "SELECT "
                "  count(*) FILTER (WHERE vt <= now()) AS ready, "
                "  count(*) FILTER (WHERE vt > now())  AS in_flight, "
                "  count(*) AS total "
                "FROM mini_cloud_jobs WHERE queue = %s",
                (queue,),
            ).fetchone()
        ready, in_flight, total = row or (0, 0, 0)
        return {"ready": int(ready), "in_flight": int(in_flight), "total": int(total)}

    def dead_letter_count(self, queue: str | None = None) -> int:
        with acquire(self._source) as conn:
            if queue is None:
                row = conn.execute("SELECT count(*) FROM mini_cloud_dead_letter").fetchone()
            else:
                row = conn.execute(
                    "SELECT count(*) FROM mini_cloud_dead_letter WHERE queue = %s", (queue,)
                ).fetchone()
        return int(row[0]) if row else 0

    def purge(self, queue: str) -> int:
        """Delete all live jobs in a queue (not the dead-letter table). Returns rows removed."""
        with acquire(self._source) as conn:
            cur = conn.execute("DELETE FROM mini_cloud_jobs WHERE queue = %s", (queue,))
            if not conn.autocommit:
                conn.commit()
            return cur.rowcount

    def requeue_dead_letter(
        self, queue: str, *, job_id: int | None = None, max_attempts: int = 5
    ) -> int:
        """Replay dead-lettered jobs back onto the live queue. Returns the number requeued.

        The admin counterpart to :meth:`nack`'s automatic dead-lettering: after the cause of a
        poison message is fixed, move it back so it runs again. Requeues one job by ``job_id``, or
        every dead-lettered job in ``queue`` when ``job_id`` is ``None``. Requeued jobs come back
        with ``attempts`` reset to 0, visible immediately, and no ``dedupe_key`` (the dead-letter
        table doesn't retain it, and a stale key must not block the replay). A fresh ``id`` is
        assigned; the dead-letter rows are removed in the same transaction so a job is never both
        dead and live.
        """
        sql = """
            WITH moved AS (
                DELETE FROM mini_cloud_dead_letter
                WHERE queue = %s AND (%s::bigint IS NULL OR id = %s)
                RETURNING queue, payload
            )
            INSERT INTO mini_cloud_jobs (queue, payload, priority, max_attempts, dedupe_key, vt)
            SELECT queue, payload, 0, %s, NULL, now() FROM moved
            RETURNING id
        """
        with acquire(self._source) as conn:
            cur = conn.execute(sql, (queue, job_id, job_id, max_attempts))
            if not conn.autocommit:
                conn.commit()
            return cur.rowcount

    # --- worker loop ----------------------------------------------------------------
    def work_once(
        self,
        queue: str,
        handler: Callable[[Job], Any],
        *,
        visibility_timeout: float = 30.0,
        worker_id: str | None = None,
    ) -> bool:
        """Reserve one job and run ``handler``. ack on success; on :class:`RetryLater` or any
        exception, nack (backoff or dead-letter). Returns ``True`` if a job was processed,
        ``False`` if the queue was empty."""
        job = self.dequeue(queue, visibility_timeout=visibility_timeout, worker_id=worker_id)
        if job is None:
            return False
        try:
            handler(job)
        except RetryLater as rl:
            self.nack(job, delay_seconds=rl.delay_seconds, error=repr(rl))
        except Exception as exc:  # noqa: BLE001 — a failed handler must not kill the worker loop
            _log.exception("job %s (queue=%s) failed", job.id, queue)
            requeued = self.nack(job, error=repr(exc))
            if not requeued:
                _log.error("job %s dead-lettered after %d attempts", job.id, job.attempts)
        else:
            self.ack(job)
        return True

    def run_worker(
        self,
        queue: str,
        handler: Callable[[Job], Any],
        *,
        visibility_timeout: float = 30.0,
        worker_id: str | None = None,
        poll_interval: float = 1.0,
        stop: Callable[[], bool] | None = None,
    ) -> None:
        """Block, processing jobs until ``stop()`` returns True (default: never — Ctrl-C to exit).
        Sleeps ``poll_interval`` when the queue drains. This is the simple in-process worker; a
        heavier app can run several of these in threads/processes."""
        _log.info("worker started on queue=%s (id=%s)", queue, worker_id)
        while not (stop and stop()):
            worked = self.work_once(
                queue, handler, visibility_timeout=visibility_timeout, worker_id=worker_id
            )
            if not worked:
                time.sleep(poll_interval)


def _row_to_job(row: tuple[Any, ...]) -> Job:
    return Job(
        id=int(row[0]),
        queue=row[1],
        payload=row[2] if isinstance(row[2], dict) else dict(row[2]),
        priority=int(row[3]),
        attempts=int(row[4]),
        max_attempts=int(row[5]),
        dedupe_key=row[6],
        created_at=row[7],
    )
