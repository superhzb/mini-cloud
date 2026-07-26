"""Live queue tests — exercise the real Postgres semantics. Skipped unless --run-live + DATABASE_URL.

These are the executable specification of the queue's contract: at-least-once delivery, visibility
timeout, retry/backoff, dead-letter, and idempotent enqueue.
"""

from __future__ import annotations

import pytest

from mini_cloud.db import Job, JobQueue, RetryLater

pytestmark = pytest.mark.live


def test_enqueue_dequeue_ack_roundtrip(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    jid = q.enqueue("t", {"n": 1})
    assert jid is not None
    job = q.dequeue("t")
    assert job is not None and job.id == jid and job.payload == {"n": 1}
    assert job.attempts == 1
    q.ack(job)
    assert q.dequeue("t") is None  # gone after ack
    assert q.depth("t")["total"] == 0


def test_skip_locked_hands_each_job_once(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"n": 1})
    first = q.dequeue("t")
    # While first is reserved (vt in the future), a second dequeue sees nothing.
    assert first is not None
    assert q.dequeue("t") is None


def test_visibility_timeout_redelivers(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"n": 1})
    first = q.dequeue("t", visibility_timeout=0.0)  # immediately visible again
    assert first is not None
    second = q.dequeue("t", visibility_timeout=30.0)
    assert second is not None and second.id == first.id
    assert second.attempts == 2  # redelivery incremented the counter


def test_nack_reschedules_then_dead_letters(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"n": 1}, max_attempts=2)
    j1 = q.dequeue("t", visibility_timeout=0.0)
    assert j1 is not None and j1.attempts == 1
    assert q.nack(j1, delay_seconds=0.0) is True  # rescheduled (attempts 1 < 2)
    j2 = q.dequeue("t", visibility_timeout=0.0)
    assert j2 is not None and j2.attempts == 2
    assert q.nack(j2, error="boom") is False  # exhausted -> dead-letter
    assert q.dead_letter_count("t") == 1
    assert q.depth("t")["total"] == 0


def test_requeue_dead_letter_replays_all(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"n": 1}, max_attempts=1)
    j = q.dequeue("t", visibility_timeout=0.0)
    assert j is not None and j.attempts == 1
    assert q.nack(j, error="boom") is False  # attempts 1 >= max_attempts 1 -> dead-letter
    assert q.dead_letter_count("t") == 1
    assert q.depth("t")["total"] == 0

    moved = q.requeue_dead_letter("t")
    assert moved == 1
    assert q.dead_letter_count("t") == 0

    again = q.dequeue("t")
    assert again is not None
    assert again.payload == {"n": 1}
    assert again.attempts == 1  # attempts were reset to 0, this dequeue is a fresh first delivery


def test_requeue_dead_letter_by_id_and_empty(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    for n in (1, 2):
        q.enqueue("t", {"n": n}, max_attempts=1)
        job = q.dequeue("t", visibility_timeout=0.0)
        assert job is not None
        assert q.nack(job, error="boom") is False
    assert q.dead_letter_count("t") == 2

    # requeue a single job by its (dead-letter) id, leaving the other dead-lettered
    with pg.cursor() as cur:  # type: ignore[attr-defined]  # pg is a live connection here
        dead_id = cur.execute("SELECT id FROM mini_cloud_dead_letter LIMIT 1").fetchone()[0]
    assert q.requeue_dead_letter("t", job_id=dead_id) == 1
    assert q.dead_letter_count("t") == 1

    assert q.requeue_dead_letter("nonexistent-queue") == 0  # nothing to move


def test_dedupe_key_prevents_duplicate(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    first = q.enqueue("t", {"n": 1}, dedupe_key="k1")
    dup = q.enqueue("t", {"n": 2}, dedupe_key="k1")
    assert first is not None
    assert dup is None  # collided; existing job stands
    assert q.depth("t")["total"] == 1


def test_priority_orders_dequeue(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"p": "low"}, priority=0)
    q.enqueue("t", {"p": "high"}, priority=10)
    job = q.dequeue("t")
    assert job is not None and job.payload["p"] == "high"


def test_work_once_acks_on_success(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"n": 1})
    seen: list[Job] = []
    assert q.work_once("t", seen.append) is True
    assert len(seen) == 1
    assert q.depth("t")["total"] == 0  # acked
    assert q.work_once("t", seen.append) is False  # empty


def test_work_once_retry_later(pg: object) -> None:
    q = JobQueue(pg)  # type: ignore[arg-type]
    q.enqueue("t", {"n": 1})

    def handler(_job: Job) -> None:
        raise RetryLater(0.0)

    assert q.work_once("t", handler, visibility_timeout=0.0) is True
    assert q.depth("t")["total"] == 1  # still there, rescheduled
