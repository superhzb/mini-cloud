"""The product-analytics tour — the funnel definition, capture helpers, and query helpers.

The Document Intelligence flow already has the ideal user journey, so we instrument a real 4-step
funnel over it (upload → process → search → chat) plus ``identify`` / ``alias`` for a demo user.
This module owns the event vocabulary and the read helpers the ``/analytics/*`` endpoints call, and
— like ``sdk_tour`` — pins the SDK's public surface with class-qualified references so any drift is
caught by the AST coverage gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mini_cloud.analytics import (
    Analytics,
    AnalyticsError,
    Event,
    EventSink,
    FunnelResult,
    FunnelStep,
    PostgresSink,
    PostHogSink,
    RetentionResult,
    funnel_sql,
    migrations_path,
    retention_sql,
    run_funnel,
    run_retention,
)
from mini_cloud.db import ConnSource, acquire

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .resources import Resources

# --- the instrumented funnel --------------------------------------------------------
EVENT_DOCUMENT_UPLOADED = "document_uploaded"  # POST /documents
EVENT_DOCUMENT_PROCESSED = "document_processed"  # worker, on pipeline completion
EVENT_SEARCH_PERFORMED = "search_performed"  # POST /search
EVENT_CHAT_STARTED = "chat_started"  # POST /documents/{id}/chat

FUNNEL_STEPS: tuple[str, ...] = (
    EVENT_DOCUMENT_UPLOADED,
    EVENT_DOCUMENT_PROCESSED,
    EVENT_SEARCH_PERFORMED,
    EVENT_CHAT_STARTED,
)

_ANON = "anonymous"

# Class-qualified references make SDK method drift mechanically visible to the AST gate — every
# public method of every exported analytics class, including the ones exercised only through the
# background flusher or the (stubbed) graduation seam. Mirrors sdk_tour.SDK_METHOD_CANARY.
ANALYTICS_METHOD_CANARY = (
    Analytics.capture,
    Analytics.identify,
    Analytics.alias,
    Analytics.flush,
    Analytics.close,
    Analytics.from_settings,
    Analytics.project,
    EventSink.write_events,
    EventSink.identify,
    EventSink.alias,
    EventSink.close,
    PostgresSink.write_events,
    PostgresSink.identify,
    PostgresSink.alias,
    PostgresSink.close,
    PostHogSink.from_settings,
    PostHogSink.write_events,
    PostHogSink.identify,
    PostHogSink.alias,
    PostHogSink.close,
)

# Exported value types the endpoints hand back — referenced concretely (annotations are strings
# under `from __future__ import annotations`, so a runtime reference is what the gate resolves).
_ANALYTICS_VALUE_TYPES = (Event, FunnelStep, FunnelResult, RetentionResult, AnalyticsError)


def resolve_actor(distinct_id: str | None, session_id: str | None) -> tuple[str, str | None]:
    """Bind who did the action for this request: the given ``distinct_id`` or a stable anonymous id
    (the session, when present). Product events should be intentional, so identity is explicit —
    passed on the request, not inferred by middleware (the v0 'explicit events only' decision)."""
    return distinct_id or (f"anon-{session_id}" if session_id else _ANON), session_id


def track(
    res: Resources,
    distinct_id: str,
    event: str,
    properties: Mapping[str, Any] | None = None,
    *,
    session_id: str | None = None,
) -> None:
    """Capture one funnel event if analytics is configured; a silent no-op otherwise so the core
    flow never depends on analytics being wired. Never blocks (the client buffers)."""
    analytics = res.analytics
    if analytics is None:
        return
    analytics.capture(distinct_id, event, properties or {}, session_id=session_id)


# --- read side (backs GET /analytics/funnel, /retention, /events) -------------------
_RECENT_EVENTS_SQL = """
    SELECT event, distinct_id, project, session_id, properties, timestamp, correlation_id
    FROM analytics_events
    WHERE project = %s
    ORDER BY id DESC
    LIMIT %s
"""


def recent_events(pool: ConnSource, project: str, *, limit: int = 20) -> list[Event]:
    """The most recent events for ``project``, newest first — the raw stream the tour exposes."""
    with acquire(pool) as conn:
        rows = conn.execute(_RECENT_EVENTS_SQL, (project, limit)).fetchall()
    return [
        Event(
            event=r[0],
            distinct_id=r[1],
            project=r[2],
            session_id=r[3],
            properties=r[4] or {},
            timestamp=r[5],
            correlation_id=r[6],
        )
        for r in rows
    ]


def showcase_funnel(pool: ConnSource, project: str) -> FunnelResult:
    """Run the instrumented 4-step funnel for ``project`` (identity resolved at query time)."""
    return run_funnel(pool, FUNNEL_STEPS, project=project)


def showcase_retention(pool: ConnSource, project: str, *, periods: int = 6) -> RetentionResult:
    """Weekly retention anchored on the first funnel step (``document_uploaded``)."""
    return run_retention(pool, EVENT_DOCUMENT_UPLOADED, project=project, periods=periods)


def sql_reference() -> dict[str, str]:
    """The generated funnel/retention SQL + the package's shipped migrations dir — an inspectable
    view of the query-time-identity machinery for the tour, exercising the pure SQL builders."""
    return {
        "funnel_sql": funnel_sql(FUNNEL_STEPS),
        "retention_sql": retention_sql(),
        "migrations_dir": str(migrations_path()),
    }
