"""ASGI (Starlette/FastAPI) glue: a middleware that wires correlation IDs, access logs, and
request metrics, plus an ``install`` one-liner that also mounts ``/metrics``.

Kept in its own module so the core ``logs``/``metrics``/``correlation`` API has no hard Starlette
dependency (install ``mini-cloud-obs[asgi]`` to use this).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .correlation import CORRELATION_HEADER, bind_correlation_id
from .logs import configure_logging, get_logger
from .metrics import observe_request, render_metrics

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mini_cloud.config import Settings
    from starlette.applications import Starlette

_log = get_logger("mini_cloud.obs.access")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Per-request: bind a correlation ID (from the ``X-Correlation-ID`` header or freshly minted),
    time the request, emit a structured access log + Prometheus metrics, and echo the correlation
    ID back on the response so a caller can trace across services."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(CORRELATION_HEADER)
        with bind_correlation_id(incoming) as cid:
            start = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception:
                duration = time.perf_counter() - start
                route = _route_template(request)
                observe_request(
                    method=request.method, route=route, status=500, duration_seconds=duration
                )
                _log.exception(
                    "request failed",
                    extra={"method": request.method, "route": route, "status": 500},
                )
                raise
            duration = time.perf_counter() - start
            route = _route_template(request)
            observe_request(
                method=request.method,
                route=route,
                status=response.status_code,
                duration_seconds=duration,
            )
            _log.info(
                "request",
                extra={
                    "method": request.method,
                    "route": route,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            response.headers[CORRELATION_HEADER] = cid
            return response


def _route_template(request: Request) -> str:
    """The matched route pattern (e.g. ``/items/{id}``) for low-cardinality metric labels; falls
    back to ``unmatched`` when no route matched (404s), never the raw path."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


async def _metrics_endpoint(_request: Request) -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


def install(app: Starlette, settings: Settings, *, metrics_path: str = "/metrics") -> None:
    """Wire observability into a Starlette/FastAPI app in one call: configure JSON logging (+Loki),
    add :class:`ObservabilityMiddleware`, and mount a ``/metrics`` endpoint for Prometheus to
    scrape. Call once at startup. This is what makes observability *on by default*."""
    configure_logging(settings)
    app.add_middleware(ObservabilityMiddleware)
    app.add_route(metrics_path, _metrics_endpoint, methods=["GET"])
