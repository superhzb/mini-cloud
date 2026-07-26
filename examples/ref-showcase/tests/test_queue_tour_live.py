"""Live tests for the db job-queue tour (pipeline.py handlers). Postgres only; run with --run-live.

Covers the queue features the happy-path fan-out doesn't: a poison job that dead-letters and is
then replayed with the new ``requeue_dead_letter``, a long job that heartbeats via ``extend``, and
correlation propagation through the payload across the enqueue boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ref_showcase.resources import Resources

pytestmark = pytest.mark.live


def test_poison_dead_letters_then_requeues(live_resources: Resources) -> None:
    from ref_showcase.pipeline import dispatch
    from ref_showcase.resources import POISON_QUEUE

    res = live_resources
    q = res.require_queue()
    q.enqueue(POISON_QUEUE, {"correlation_id": "c1"}, max_attempts=1)

    # work_once dequeues (attempts->1), the handler raises, nack sees attempts>=max -> dead-letter.
    processed = q.work_once(POISON_QUEUE, lambda job: dispatch(res, job), visibility_timeout=0.0)
    assert processed is True
    assert q.dead_letter_count(POISON_QUEUE) == 1
    assert q.depth(POISON_QUEUE)["total"] == 0

    # operator replays it once the cause is "fixed"
    assert q.requeue_dead_letter(POISON_QUEUE) == 1
    assert q.dead_letter_count(POISON_QUEUE) == 0
    assert q.depth(POISON_QUEUE)["total"] == 1


def test_long_job_heartbeats_via_extend(live_resources: Resources) -> None:
    from ref_showcase.pipeline import dispatch
    from ref_showcase.resources import LONG_QUEUE

    res = live_resources
    q = res.require_queue()
    q.enqueue(LONG_QUEUE, {"steps": 2, "correlation_id": "c2"})

    processed = q.work_once(LONG_QUEUE, lambda job: dispatch(res, job))
    assert processed is True
    assert q.depth(LONG_QUEUE)["total"] == 0  # heartbeated to completion, then acked


def test_correlation_id_survives_the_enqueue_boundary(live_resources: Resources) -> None:
    """The payload carries the correlation id; dispatch re-binds it so a handler sees it bound."""
    from mini_cloud.obs import get_correlation_id

    from ref_showcase import pipeline
    from ref_showcase.resources import LONG_QUEUE

    res = live_resources
    q = res.require_queue()
    seen: dict[str, str | None] = {}

    def probe(_res: object, _job: object) -> None:
        seen["cid"] = get_correlation_id()

    original = pipeline.HANDLERS[LONG_QUEUE]
    pipeline.HANDLERS[LONG_QUEUE] = probe  # type: ignore[assignment]  # test double
    try:
        q.enqueue(LONG_QUEUE, {"correlation_id": "trace-xyz"})
        q.work_once(LONG_QUEUE, lambda job: pipeline.dispatch(res, job))
    finally:
        pipeline.HANDLERS[LONG_QUEUE] = original

    assert seen["cid"] == "trace-xyz"
