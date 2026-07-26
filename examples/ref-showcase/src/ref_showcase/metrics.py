"""Application-level Prometheus collectors for the Document Intelligence tour.

The SDK installs the common HTTP collectors and ``/metrics`` endpoint. These collectors show the
intended extension point: apps own their business vocabulary while sharing the same registry and
scrape endpoint. Labels are deliberately bounded (known source/backend/queue/outcome values).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

DOCUMENTS_INGESTED_TOTAL = Counter(
    "documents_ingested_total",
    "Documents successfully ingested for the first time.",
    labelnames=("source",),
)

SEARCH_LATENCY_SECONDS = Histogram(
    "search_latency_seconds",
    "Semantic-search latency, including query embedding and in-app ranking.",
    labelnames=("backend",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

QUEUE_JOBS_PROCESSED_TOTAL = Counter(
    "queue_jobs_processed_total",
    "Queue handler dispatches by queue and outcome.",
    labelnames=("queue", "outcome"),
)

__all__ = [
    "DOCUMENTS_INGESTED_TOTAL",
    "SEARCH_LATENCY_SECONDS",
    "QUEUE_JOBS_PROCESSED_TOTAL",
]
