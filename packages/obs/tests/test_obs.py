"""Tests for mini_cloud.obs — logging, metrics, correlation, and the ASGI middleware."""

from __future__ import annotations

import json
import logging

from mini_cloud.config import load_settings

from mini_cloud.obs import (
    bind_correlation_id,
    configure_logging,
    get_correlation_id,
    new_correlation_id,
    observe_request,
    render_metrics,
)
from mini_cloud.obs.logs import JsonFormatter


def test_correlation_id_binds_and_restores() -> None:
    assert get_correlation_id() is None
    with bind_correlation_id("abc") as cid:
        assert cid == "abc"
        assert get_correlation_id() == "abc"
        with bind_correlation_id() as inner:
            assert get_correlation_id() == inner != "abc"
        assert get_correlation_id() == "abc"  # restored
    assert get_correlation_id() is None  # restored


def test_new_correlation_id_is_unique() -> None:
    assert new_correlation_id() != new_correlation_id()


def test_json_formatter_emits_valid_json_with_fields() -> None:
    fmt = JsonFormatter(app_name="ref", app_env="dev")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", None, None)
    record.__dict__["user_id"] = 7  # simulate extra=
    with bind_correlation_id("cid-1"):
        out = fmt.format(record)
    obj = json.loads(out)
    assert obj["msg"] == "hello"
    assert obj["level"] == "info"
    assert obj["app"] == "ref"
    assert obj["env"] == "dev"
    assert obj["correlation_id"] == "cid-1"
    assert obj["user_id"] == 7
    assert obj["ts"].endswith("Z")


def test_configure_logging_sets_single_stdout_handler() -> None:
    settings = load_settings(environ={"APP_NAME": "ref", "LOG_LEVEL": "warn"})
    logger = configure_logging(settings)
    root = logging.getLogger()
    assert len(root.handlers) == 1  # no Loki handler without LOKI_URL
    assert root.level == logging.WARNING
    assert logger.name == "ref"


def test_configure_logging_adds_loki_when_url_present() -> None:
    settings = load_settings(environ={"APP_NAME": "ref", "LOKI_URL": "http://127.0.0.1:3100"})
    configure_logging(settings)
    root = logging.getLogger()
    handler_names = {type(h).__name__ for h in root.handlers}
    assert "LokiHandler" in handler_names
    # clean up the daemon thread handler
    for h in list(root.handlers):
        root.removeHandler(h)


def test_metrics_render_and_observe() -> None:
    observe_request(method="GET", route="/x", status=200, duration_seconds=0.01)
    body, content_type = render_metrics()
    assert b"http_requests_total" in body
    assert "text/plain" in content_type


def test_asgi_middleware_end_to_end() -> None:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from mini_cloud.obs.asgi import install
    from mini_cloud.obs.correlation import CORRELATION_HEADER

    async def hello(_request: object) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/hello", hello)])
    install(app, load_settings(environ={"APP_NAME": "ref"}))
    client = TestClient(app)

    resp = client.get("/hello")
    assert resp.status_code == 200
    assert CORRELATION_HEADER in resp.headers  # echoed back

    # supplied correlation id is honoured
    resp2 = client.get("/hello", headers={CORRELATION_HEADER: "trace-42"})
    assert resp2.headers[CORRELATION_HEADER] == "trace-42"

    # /metrics is mounted and shows the request we just made
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert b"http_requests_total" in metrics.content
