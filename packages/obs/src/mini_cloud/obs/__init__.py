"""mini_cloud.obs — structured logs → Loki, metrics → Prometheus, correlation-ID propagation.

Retires per-app logging env + scattered JSONL directories with one shared pane of glass (the
infra stack's Grafana). Observability is *on by default* (no opt-in flag) — scorecard metric #7.

    from mini_cloud.config import load_settings
    from mini_cloud.obs import get_logger, bind_correlation_id
    from mini_cloud.obs.asgi import install    # needs mini-cloud-obs[asgi]

    settings = load_settings()
    install(app, settings)            # JSON logging (+Loki), request metrics, /metrics endpoint
    log = get_logger(__name__)
    log.info("started", extra={"port": settings.port})

    # correlate a non-HTTP unit of work (e.g. a job handler):
    with bind_correlation_id():
        do_work()

The ASGI middleware lives in ``mini_cloud.obs.asgi`` to keep this core import Starlette-free.
"""

from __future__ import annotations

from .correlation import (
    CORRELATION_HEADER,
    bind_correlation_id,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)
from .logs import JsonFormatter, LokiHandler, configure_logging, get_logger
from .metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    observe_request,
    render_metrics,
)

__version__ = "0.1.0"

__all__ = [
    # correlation
    "CORRELATION_HEADER",
    "bind_correlation_id",
    "get_correlation_id",
    "set_correlation_id",
    "new_correlation_id",
    # logs
    "configure_logging",
    "get_logger",
    "JsonFormatter",
    "LokiHandler",
    # metrics
    "observe_request",
    "render_metrics",
    "HTTP_REQUESTS_TOTAL",
    "HTTP_REQUEST_DURATION_SECONDS",
]
