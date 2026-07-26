"""Live analytics tour — needs a real analytics Postgres (MINI_ANALYTICS_DSN).

Proves the write path (PostgresSink + the buffered Analytics client), query-time identity
resolution (an alias collapses anonymous → identified across a funnel), and the seeded stream
driving funnel + retention. Skips without MINI_ANALYTICS_DSN; runs under `check-live`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

pytestmark = pytest.mark.live

if TYPE_CHECKING:
    from mini_cloud.analytics import Event
    from mini_cloud.db import ConnSource

_PROJECT = "test-analytics"
_BASE = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _event(event: str, distinct_id: str, minute: int) -> Event:
    from mini_cloud.analytics import Event

    return Event(
        event=event,
        distinct_id=distinct_id,
        project=_PROJECT,
        properties={"seeded": True},
        timestamp=_BASE + timedelta(minutes=minute),
    )


def test_funnel_resolves_alias_across_anonymous_and_identified(analytics_pool: ConnSource) -> None:
    from mini_cloud.analytics import PostgresSink, run_funnel

    from ref_showcase.analytics_tour import FUNNEL_STEPS

    sink = PostgresSink(analytics_pool)
    # One person: first two steps anonymous, then aliased, then the last two steps identified.
    sink.write_events(
        [
            _event(FUNNEL_STEPS[0], "anon-1", 0),
            _event(FUNNEL_STEPS[1], "anon-1", 5),
            _event(FUNNEL_STEPS[2], "user-1", 10),
            _event(FUNNEL_STEPS[3], "user-1", 15),
        ]
    )
    sink.alias("anon-1", "user-1")

    result = run_funnel(analytics_pool, FUNNEL_STEPS, project=_PROJECT)
    # Without alias resolution this would be two half-funnels; resolved, it's one full conversion.
    assert [s.count for s in result.steps] == [1, 1, 1, 1]
    assert result.entered == 1
    assert result.converted == 1
    assert result.overall_conversion == pytest.approx(1.0)


def test_funnel_counts_drop_off_and_respect_order(analytics_pool: ConnSource) -> None:
    from mini_cloud.analytics import PostgresSink, run_funnel

    from ref_showcase.analytics_tour import FUNNEL_STEPS

    sink = PostgresSink(analytics_pool)
    # u1 completes all four; u2 stops after step 1; u3 only enters.
    sink.write_events(
        [
            *(_event(step, "u1", i * 5) for i, step in enumerate(FUNNEL_STEPS)),
            _event(FUNNEL_STEPS[0], "u2", 0),
            _event(FUNNEL_STEPS[1], "u2", 5),
            _event(FUNNEL_STEPS[0], "u3", 0),
        ]
    )
    result = run_funnel(analytics_pool, FUNNEL_STEPS, project=_PROJECT)
    assert [s.count for s in result.steps] == [3, 2, 1, 1]
    assert result.steps[1].conversion_from_top == pytest.approx(2 / 3)


def test_client_capture_flush_then_query(analytics_pool: ConnSource) -> None:
    from mini_cloud.analytics import Analytics, PostgresSink

    from ref_showcase.analytics_tour import recent_events

    analytics = Analytics(PostgresSink(analytics_pool), project=_PROJECT, flush_interval=0.05)
    try:
        analytics.capture("user-7", "custom_event", {"k": 1})
        analytics.flush()
        events = recent_events(analytics_pool, _PROJECT, limit=10)
        assert any(e.event == "custom_event" and e.distinct_id == "user-7" for e in events)
    finally:
        analytics.close()


def test_seeded_stream_drives_funnel_and_retention(analytics_pool: ConnSource) -> None:
    from mini_cloud.analytics import run_funnel, run_retention

    from ref_showcase.analytics_tour import EVENT_DOCUMENT_UPLOADED, FUNNEL_STEPS
    from ref_showcase.seed import ANALYTICS_SEED_USERS, seed_analytics_events

    written = seed_analytics_events(analytics_pool, _PROJECT)
    assert written > 0

    funnel = run_funnel(analytics_pool, FUNNEL_STEPS, project=_PROJECT)
    assert funnel.entered == ANALYTICS_SEED_USERS  # everyone enters step 1
    counts = [s.count for s in funnel.steps]
    assert counts == sorted(counts, reverse=True)  # monotonic drop-off
    assert funnel.converted < funnel.entered

    retention = run_retention(analytics_pool, EVENT_DOCUMENT_UPLOADED, project=_PROJECT)
    assert retention.cells  # at least the period-0 cohorts
    assert all(period >= 0 for _, period, _ in retention.cells)


def test_seed_is_idempotent(analytics_pool: ConnSource) -> None:
    from ref_showcase.seed import seed_analytics_events

    first = seed_analytics_events(analytics_pool, _PROJECT)
    second = seed_analytics_events(analytics_pool, _PROJECT)
    assert first == second
    # Re-seeding replaces rather than accumulates: total seeded events equals one run's worth.
    with analytics_pool.connection() as conn:  # type: ignore[union-attr]  # a pool in the live path
        total = conn.execute(
            "SELECT count(*) FROM analytics_events WHERE project = %s AND "
            "(properties->>'seeded') = 'true'",
            (_PROJECT,),
        ).fetchone()
    assert total is not None and total[0] == second
