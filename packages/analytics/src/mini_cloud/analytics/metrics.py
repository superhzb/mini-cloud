"""Prometheus collectors for the analytics client's own health.

The batched flush is *honest about backpressure*: when the bounded buffer is full, ``capture()``
drops the event and increments :data:`ANALYTICS_EVENTS_DROPPED_TOTAL` rather than blocking the
request path — the same contract the real PostHog client makes. These collectors register on the
default Prometheus registry, so any app that already exposes ``/metrics`` (via ``mini_cloud.obs``)
surfaces them with no extra wiring.
"""

from __future__ import annotations

from prometheus_client import Counter

# Low-cardinality: labelled only by project (bounded — one client, one project).
ANALYTICS_EVENTS_CAPTURED_TOTAL = Counter(
    "analytics_events_captured_total",
    "Product-analytics events accepted into the in-process buffer.",
    labelnames=("project",),
)

ANALYTICS_EVENTS_DROPPED_TOTAL = Counter(
    "analytics_events_dropped_total",
    "Product-analytics events dropped because the buffer was full (backpressure).",
    labelnames=("project",),
)

ANALYTICS_FLUSH_ERRORS_TOTAL = Counter(
    "analytics_flush_errors_total",
    "Batched flushes that failed to write to the sink (the batch is dropped, app never blocks).",
    labelnames=("project",),
)
