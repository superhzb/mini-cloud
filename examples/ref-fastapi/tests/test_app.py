"""Unit tests for ref-fastapi — no services required.

They boot the app with no backing services configured: liveness must still answer, readiness must
report not-ready, and observability (/metrics) must be wired regardless.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ref_fastapi.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> Iterator[TestClient]:
    # No DATABASE_URL / STORAGE_* → resources are all None; the app still boots.
    for var in (
        "DATABASE_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "MINI_INFERENCE_URL",
        "LOKI_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_NAME", "ref-fastapi-test")
    # Run from a clean dir so load_settings() doesn't pick up a real ./.env (which would point the
    # app at services this no-services test can't reach). This keeps `make check` green with the
    # infra stack down.
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]  # tmp_path is a Path
    # `with` triggers the lifespan so app.state.resources is populated (as in production).
    with TestClient(create_app()) as c:
        yield c


def test_healthz_is_liveness_only(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_not_ready_without_services(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False


def test_root_lists_endpoints(client: TestClient) -> None:
    body = client.get("/").json()
    assert body["metrics"] == "/metrics"
    assert "docs" in body


def test_metrics_endpoint_wired(client: TestClient) -> None:
    client.get("/healthz")  # generate one request
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"http_requests_total" in resp.content


def test_correlation_id_echoed(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"X-Correlation-ID": "trace-9"})
    assert resp.headers["X-Correlation-ID"] == "trace-9"


def test_notes_requires_storage(client: TestClient) -> None:
    # With no storage configured, the demo route can't run — it raises (500), not a silent success.
    with pytest.raises(RuntimeError, match="storage unavailable"):
        client.post("/notes", json={"text": "hi"})
