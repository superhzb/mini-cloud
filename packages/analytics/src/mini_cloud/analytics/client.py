"""The :class:`Analytics` client — PostHog-compatible ``capture`` / ``identify`` / ``alias``.

``capture()`` never blocks the request path: events go into a bounded in-process buffer that a
daemon thread flushes on size/interval and at shutdown. When the buffer is full the event is
dropped and :data:`~mini_cloud.analytics.metrics.ANALYTICS_EVENTS_DROPPED_TOTAL` is incremented —
honest backpressure, the same contract the real PostHog client makes. ``identify`` / ``alias`` are
low-frequency person-graph writes and go straight to the sink.

    from mini_cloud.config import load_settings
    from mini_cloud.db import make_pool
    from mini_cloud.analytics import Analytics

    settings = load_settings()
    pool = make_pool(settings.require("analytics_dsn"))
    analytics = Analytics.from_settings(settings, source=pool)   # backend chosen by env

    analytics.capture("user-42", "document_uploaded", {"bytes": 1024})
    analytics.identify("user-42", {"plan": "pro"})
    analytics.close()                                            # flush + stop the thread
"""

from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Full, Queue
from typing import TYPE_CHECKING, Any

from .events import Event
from .metrics import (
    ANALYTICS_EVENTS_CAPTURED_TOTAL,
    ANALYTICS_EVENTS_DROPPED_TOTAL,
    ANALYTICS_FLUSH_ERRORS_TOTAL,
)
from .sinks import AnalyticsError, EventSink, PostgresSink, PostHogSink

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from mini_cloud.config import Settings
    from mini_cloud.db import ConnSource

_log = logging.getLogger("mini_cloud.analytics")

# Correlation stitching is a soft dependency: if obs is installed, capture() auto-tags events with
# the current correlation id so a product event traces back to the request that produced it. Absent
# obs, it's simply None — analytics does not hard-depend on obs.
try:  # pragma: no cover - trivial import shim
    from mini_cloud.obs.correlation import get_correlation_id as _get_correlation_id
except Exception:  # noqa: BLE001 - obs is optional

    def _get_correlation_id() -> str | None:
        return None


class Analytics:
    """A PostHog-compatible product-analytics client over a pluggable :class:`EventSink`."""

    def __init__(
        self,
        sink: EventSink,
        *,
        project: str,
        flush_interval: float = 5.0,
        max_batch: int = 100,
        max_buffer: int = 10_000,
    ) -> None:
        if not project:
            raise AnalyticsError("Analytics requires a non-empty project")
        self._sink = sink
        self._project = project
        self._queue: Queue[Event] = Queue(maxsize=max_buffer)
        self._flush_interval = flush_interval
        self._max_batch = max_batch
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="analytics-flush", daemon=True)
        self._thread.start()

    @property
    def project(self) -> str:
        """The project every event from this client is tagged with."""
        return self._project

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        source: ConnSource | None = None,
        project: str | None = None,
        sink: EventSink | None = None,
        **kwargs: Any,  # noqa: ANN401 — forwarded to __init__ (flush_interval/max_batch/…)
    ) -> Analytics:
        """Build from canonical settings, choosing the sink from ``MINI_ANALYTICS_BACKEND``.

        ``project`` defaults to ``MINI_ANALYTICS_PROJECT`` and then ``APP_NAME`` — so an app that
        names itself is identified with no extra config, exactly like ``InferenceClient``. Pass
        ``source`` (a pool/connection to the analytics DB) for the default ``postgres`` backend, or
        an explicit ``sink`` to bypass backend selection (tests use a fake sink).
        """
        resolved = project or settings.analytics_project or settings.app_name
        if not resolved:
            raise AnalyticsError(
                "no analytics project — set MINI_ANALYTICS_PROJECT or APP_NAME, or pass project="
            )
        if sink is None:
            sink = _sink_from_settings(settings, source=source)
        return cls(sink, project=resolved, **kwargs)

    # --- producer surface (PostHog-compatible) --------------------------------------
    def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Mapping[str, Any] | None = None,
        timestamp: datetime | None = None,
        *,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Buffer one event for ``distinct_id``. Never blocks; drops (and counts) if the buffer
        is full. ``correlation_id`` defaults to the current obs correlation id when obs is set."""
        evt = Event(
            event=event,
            distinct_id=distinct_id,
            project=self._project,
            properties=dict(properties or {}),
            timestamp=timestamp,
            session_id=session_id,
            correlation_id=correlation_id or _get_correlation_id(),
        )
        try:
            self._queue.put_nowait(evt)
        except Full:
            ANALYTICS_EVENTS_DROPPED_TOTAL.labels(project=self._project).inc()
            return
        ANALYTICS_EVENTS_CAPTURED_TOTAL.labels(project=self._project).inc()

    def identify(self, distinct_id: str, properties: Mapping[str, Any] | None = None) -> None:
        """Upsert a person and their properties (a direct sink write, not buffered)."""
        self._sink.identify(distinct_id, dict(properties or {}))

    def alias(self, previous_id: str, distinct_id: str) -> None:
        """Stitch an anonymous ``previous_id`` to an identified ``distinct_id`` (direct write)."""
        self._sink.alias(previous_id, distinct_id)

    # --- lifecycle ------------------------------------------------------------------
    def flush(self) -> None:
        """Synchronously drain and write everything currently buffered. Safe to call any time."""
        batch = self._drain_all()
        if batch:
            self._flush_batch(batch)

    def close(self) -> None:
        """Stop the flush thread, flush the remainder, and close the sink. Idempotent-ish."""
        self._stop.set()
        self._thread.join(timeout=self._flush_interval + 5.0)
        self.flush()
        self._sink.close()

    # --- internals ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._drain_window()
            if batch:
                self._flush_batch(batch)

    def _drain_window(self) -> list[Event]:
        """Collect up to ``max_batch`` events within one flush interval (blocking on the first)."""
        batch: list[Event] = []
        deadline = time.time() + self._flush_interval
        while len(batch) < self._max_batch and time.time() < deadline:
            try:
                batch.append(self._queue.get(timeout=self._flush_interval))
            except Empty:
                break
        return batch

    def _drain_all(self) -> list[Event]:
        batch: list[Event] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except Empty:
                return batch

    def _flush_batch(self, batch: list[Event]) -> None:
        try:
            self._sink.write_events(batch)
        except Exception:  # noqa: BLE001 — analytics must never crash the app; drop + count
            ANALYTICS_FLUSH_ERRORS_TOTAL.labels(project=self._project).inc()
            _log.warning("analytics flush failed; dropped %d events", len(batch), exc_info=True)


def _sink_from_settings(settings: Settings, *, source: ConnSource | None) -> EventSink:
    """Resolve the sink for ``MINI_ANALYTICS_BACKEND``."""
    if settings.analytics_backend == "posthog":
        return PostHogSink.from_settings(settings)
    if source is None:
        raise AnalyticsError(
            "the default 'postgres' analytics backend needs a ConnSource — pass source= "
            "(a pool to MINI_ANALYTICS_DSN) to Analytics.from_settings"
        )
    return PostgresSink(source)
