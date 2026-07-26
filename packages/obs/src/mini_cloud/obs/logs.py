"""Structured JSON logging with correlation IDs, and an optional Loki push handler.

Every log line is one JSON object (``ts``, ``level``, ``logger``, ``msg``, ``app``, ``env``,
``correlation_id`` + any ``extra``) — the canonical JSONL format from the app conventions. Two
sinks:

* **stdout** (always): a native process under ``brbot-router`` writes JSON to stdout; the router
  captures it, and it's greppable/tailable immediately.
* **Loki** (optional): if ``LOKI_URL`` is set, a background-batching :class:`LokiHandler` also
  pushes lines to Loki so they land in the shared Grafana. Best-effort — a Loki hiccup never
  blocks or crashes the app.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import urllib.request
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

from .correlation import get_correlation_id

if TYPE_CHECKING:
    from mini_cloud.config import Settings

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as one compact JSON object."""

    def __init__(self, *, app_name: str | None = None, app_env: str = "dev") -> None:
        super().__init__()
        self.app_name = app_name
        self.app_env = app_env

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if self.app_name:
            payload["app"] = self.app_name
        payload["env"] = self.app_env
        cid = get_correlation_id()
        if cid:
            payload["correlation_id"] = cid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Merge structured extras passed as logger.info("...", extra={"k": v}).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str, separators=(",", ":"))


class LokiHandler(logging.Handler):
    """Best-effort background handler that pushes JSON log lines to Loki's HTTP push API.

    Records are enqueued and flushed by a daemon thread in batches, so logging never blocks on the
    network. On any push error the batch is dropped (stdout remains the source of truth). Uses
    stdlib ``urllib`` — no extra dependency.
    """

    def __init__(
        self,
        loki_url: str,
        *,
        labels: dict[str, str] | None = None,
        flush_interval: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        super().__init__()
        self._push_url = loki_url.rstrip("/") + "/loki/api/v1/push"
        self._labels = labels or {}
        self._queue: Queue[tuple[float, str]] = Queue(maxsize=10_000)
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="loki-push", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            self._queue.put_nowait((time.time(), line))
        except Exception:  # noqa: BLE001 — logging must never raise into the app
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._drain()
            if batch:
                self._push(batch)
            else:
                time.sleep(self._flush_interval)

    def _drain(self) -> list[tuple[float, str]]:
        batch: list[tuple[float, str]] = []
        deadline = time.time() + self._flush_interval
        while len(batch) < self._batch_size and time.time() < deadline:
            try:
                batch.append(self._queue.get(timeout=self._flush_interval))
            except Empty:
                break
        return batch

    def _push(self, batch: list[tuple[float, str]]) -> None:
        values = [[f"{int(ts * 1e9)}", line] for ts, line in batch]
        body = json.dumps({"streams": [{"stream": self._labels, "values": values}]}).encode()
        req = urllib.request.Request(
            self._push_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()  # noqa: S310 — fixed internal Loki URL
        except Exception:  # noqa: BLE001 — best-effort; stdout is the source of truth
            pass

    def close(self) -> None:
        self._stop.set()
        super().close()


def configure_logging(settings: Settings) -> logging.Logger:
    """Configure root logging for a mini-cloud app from canonical settings; return the app logger.

    Idempotent-ish: clears existing handlers on the root so repeated calls (tests, reloads) don't
    duplicate output. Always adds a JSON stdout handler; adds a :class:`LokiHandler` when
    ``LOKI_URL`` is set. Log level comes from ``LOG_LEVEL``.
    """
    level = getattr(logging, _level_name(settings.log_level), logging.INFO)
    formatter = JsonFormatter(app_name=settings.app_name, app_env=settings.app_env)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)
    root.addHandler(stdout)

    if settings.loki_url:
        loki = LokiHandler(
            settings.loki_url,
            labels={"app": settings.app_name or "app", "env": settings.app_env},
        )
        loki.setFormatter(formatter)
        root.addHandler(loki)

    return logging.getLogger(settings.app_name or "app")


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Assumes :func:`configure_logging` has run (falls back to a plain
    logger otherwise)."""
    return logging.getLogger(name)


def _level_name(level: str) -> str:
    return {"warn": "WARNING"}.get(level, level.upper())
