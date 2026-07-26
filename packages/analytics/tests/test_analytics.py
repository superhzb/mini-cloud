"""Offline unit tests for mini_cloud.analytics — a fake in-memory sink, no database.

Covers the client contract (buffering, flush, close, drop-on-overflow, identity writes) and the
pure SQL builders. Live tests against a real analytics Postgres are gated separately.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from mini_cloud.analytics import (
    Analytics,
    AnalyticsError,
    Event,
    EventSink,
    FunnelResult,
    funnel_sql,
    migrations_path,
    retention_sql,
)


class FakeSink:
    """An in-memory :class:`EventSink` — records everything, thread-safe for the flush thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[Event] = []
        self.persons: list[tuple[str, dict[str, Any]]] = []
        self.aliases: list[tuple[str, str]] = []
        self.closed = False

    def write_events(self, events: Any) -> None:
        with self._lock:
            self.events.extend(events)

    def identify(self, distinct_id: str, properties: Any) -> None:
        with self._lock:
            self.persons.append((distinct_id, dict(properties)))

    def alias(self, previous_id: str, distinct_id: str) -> None:
        with self._lock:
            self.aliases.append((previous_id, distinct_id))

    def close(self) -> None:
        self.closed = True

    def snapshot_events(self) -> list[Event]:
        with self._lock:
            return list(self.events)


def _client(sink: FakeSink, **kwargs: Any) -> Analytics:
    return Analytics(sink, project="test-project", flush_interval=0.02, **kwargs)


def test_fake_sink_satisfies_protocol() -> None:
    assert isinstance(FakeSink(), EventSink)


def test_capture_flush_close_roundtrip() -> None:
    sink = FakeSink()
    a = _client(sink)
    a.capture("u1", "signed_up", {"plan": "pro"})
    a.capture("u1", "document_uploaded")
    a.flush()
    a.close()

    assert sink.closed is True
    events = sink.snapshot_events()
    assert [e.event for e in events] == ["signed_up", "document_uploaded"]
    first = events[0]
    assert first.distinct_id == "u1"
    assert first.project == "test-project"
    assert first.properties == {"plan": "pro"}


def test_background_thread_flushes_without_explicit_flush() -> None:
    sink = FakeSink()
    a = _client(sink)
    for i in range(5):
        a.capture(f"u{i}", "ping")
    # The daemon thread should drain within a few flush intervals.
    deadline = 2.0
    waited = 0.0
    while len(sink.snapshot_events()) < 5 and waited < deadline:
        import time

        time.sleep(0.05)
        waited += 0.05
    a.close()
    assert len(sink.snapshot_events()) == 5


def test_capture_never_raises_and_drops_on_overflow() -> None:
    sink = FakeSink()
    # Stop the flush thread from draining so the buffer actually fills.
    a = Analytics(sink, project="p", flush_interval=1000.0, max_buffer=3)
    a._stop.set()  # noqa: SLF001 — freeze the flusher for a deterministic overflow test
    for i in range(10):
        a.capture(f"u{i}", "e")  # must never raise even past capacity
    from mini_cloud.analytics.metrics import ANALYTICS_EVENTS_DROPPED_TOTAL

    dropped = ANALYTICS_EVENTS_DROPPED_TOTAL.labels(project="p")._value.get()  # noqa: SLF001
    assert dropped >= 7  # 10 captured, buffer holds 3


def test_identify_and_alias_write_through() -> None:
    sink = FakeSink()
    a = _client(sink)
    a.identify("u1", {"email": "a@b.c"})
    a.alias("anon-xyz", "u1")
    a.close()
    assert sink.persons == [("u1", {"email": "a@b.c"})]
    assert sink.aliases == [("anon-xyz", "u1")]


def test_project_defaults_and_property() -> None:
    sink = FakeSink()
    a = _client(sink)
    assert a.project == "test-project"
    a.close()


def test_empty_project_rejected() -> None:
    with pytest.raises(AnalyticsError):
        Analytics(FakeSink(), project="")


def test_from_settings_uses_explicit_sink_and_app_name_default() -> None:
    from mini_cloud.config import load_settings

    settings = load_settings(environ={"APP_NAME": "my-app"})
    sink = FakeSink()
    a = Analytics.from_settings(settings, sink=sink, flush_interval=0.02)
    assert a.project == "my-app"  # MINI_ANALYTICS_PROJECT unset -> APP_NAME
    a.close()


def test_from_settings_postgres_backend_requires_source() -> None:
    from mini_cloud.config import load_settings

    settings = load_settings(environ={"APP_NAME": "my-app"})  # backend defaults to postgres
    with pytest.raises(AnalyticsError, match="ConnSource"):
        Analytics.from_settings(settings)  # no source, no sink


def test_from_settings_posthog_backend_is_stubbed() -> None:
    from mini_cloud.config import load_settings

    settings = load_settings(environ={"APP_NAME": "a", "MINI_ANALYTICS_BACKEND": "posthog"})
    with pytest.raises(AnalyticsError, match="posthog"):
        Analytics.from_settings(settings)


# --- pure SQL builders (offline) ----------------------------------------------------
def test_funnel_sql_has_one_filter_per_step() -> None:
    sql = funnel_sql(["a", "b", "c"])
    assert sql.count("FILTER (WHERE event = %s)") == 3
    # ordered: the third step's count requires the first two timestamps to be present and monotonic
    assert "s2 >= s1" in sql
    assert "s1 >= s0" in sql
    assert "analytics_person_aliases" in sql  # query-time identity resolution


def test_funnel_sql_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        funnel_sql([])


def test_retention_sql_is_weekly_and_resolves_identity() -> None:
    sql = retention_sql()
    assert "date_trunc('week'" in sql
    assert "analytics_person_aliases" in sql


def test_funnel_result_shape() -> None:
    # Construct directly to pin the dataclass contract the endpoint depends on.
    from mini_cloud.analytics import FunnelStep

    r = FunnelResult(
        steps=[FunnelStep("a", 10, 1.0, 1.0), FunnelStep("b", 4, 0.4, 0.4)],
        entered=10,
        converted=4,
        overall_conversion=0.4,
    )
    assert r.overall_conversion == 0.4
    assert r.steps[1].event == "b"


def test_migrations_path_points_at_shipped_sql() -> None:
    path = migrations_path()
    assert path.is_dir()
    assert any(p.name.endswith(".sql") for p in path.iterdir())
