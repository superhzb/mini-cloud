"""mini_cloud.analytics — Mixpanel-style product analytics on the shared mini-cloud Postgres.

Answers a different question than ``mini_cloud.obs``. ``obs`` asks *"is the service healthy?"*
(aggregated counters/gauges/logs, no identity). Analytics asks *"did **this person** go
upload → process → search → chat, and where did they drop off?"* — an append-only, per-person,
timestamped event store, with funnels and retention over it.

The client mirrors PostHog's ``capture`` / ``identify`` / ``alias`` on purpose: a maturing demo can
flip ``MINI_ANALYTICS_BACKEND=posthog`` and ship to real PostHog by changing env, not code.

    from mini_cloud.config import load_settings
    from mini_cloud.db import make_pool, migrate
    from mini_cloud.analytics import Analytics, migrations_path, run_funnel

    settings = load_settings()
    pool = make_pool(settings.require("analytics_dsn"))   # a SEPARATE db from DATABASE_URL
    migrate(pool, migrations_path())                       # this package ships its own schema

    analytics = Analytics.from_settings(settings, source=pool)
    analytics.capture("user-42", "document_uploaded")
    result = run_funnel(pool, ["document_uploaded", "search_performed"], project=analytics.project)

The event store (``PostgresSink``) and the graduation seam (``PostHogSink``) sit behind the
:class:`EventSink` protocol; funnel/retention SQL resolves anonymous→identified identity at query
time (see ``funnels``). ``posthog-python`` is an optional ``[posthog]`` extra — the core imports
clean, obs-style.
"""

from __future__ import annotations

from pathlib import Path

from .client import Analytics
from .events import Event
from .funnels import (
    FunnelResult,
    FunnelStep,
    RetentionResult,
    funnel_sql,
    retention_sql,
    run_funnel,
    run_retention,
)
from .sinks import AnalyticsError, EventSink, PostgresSink, PostHogSink

__version__ = "0.1.0"

__all__ = [
    # client
    "Analytics",
    "AnalyticsError",
    "Event",
    # sinks
    "EventSink",
    "PostgresSink",
    "PostHogSink",
    # funnels / retention
    "FunnelStep",
    "FunnelResult",
    "RetentionResult",
    "run_funnel",
    "funnel_sql",
    "run_retention",
    "retention_sql",
    # schema
    "migrations_path",
]


def migrations_path() -> Path:
    """Absolute path to this package's ordered ``NNNN_*.sql`` migrations.

    A consumer applies them against ``MINI_ANALYTICS_DSN`` (a *separate* DB from the app's own):
    ``migrate(analytics_pool, migrations_path())``. This is the first SDK package that ships and
    applies its own migrations — the schema travels with the code that depends on it.
    """
    return Path(__file__).parent / "migrations"
