"""Prometheus metrics — scrape-first (an app exposes ``/metrics``; Prometheus scrapes it).

Ships the standard HTTP request metrics every app should emit *by default* (no opt-in flag — that
is scorecard metric #7). Apps add their own domain metrics with plain ``prometheus_client``
counters/histograms; this module just standardises the request-level ones and the exposition
endpoint.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Labels kept low-cardinality: method + matched route template (never the raw path) + status.
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    labelnames=("method", "route", "status"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def observe_request(*, method: str, route: str, status: int, duration_seconds: float) -> None:
    """Record one completed HTTP request. Called by the ASGI middleware; callable directly for
    non-ASGI frameworks."""
    HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=str(status)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(duration_seconds)


def render_metrics() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for a ``GET /metrics`` handler (framework-agnostic)."""
    return generate_latest(), CONTENT_TYPE_LATEST
