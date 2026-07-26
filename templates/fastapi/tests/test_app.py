"""Unit tests — no services required. Liveness answers, readiness reports not-ready, obs is wired."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from {{package}}.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> Iterator[TestClient]:
    for var in ("DATABASE_URL", "STORAGE_ENDPOINT", "STORAGE_BUCKET", "MINI_INFERENCE_URL", "LOKI_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_NAME", "{{name}}-test")
    # Run from a clean dir so load_settings() ignores the real ./.env — keeps `make check` green
    # with no services running.
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    with TestClient(create_app()) as c:
        yield c


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_not_ready_without_services(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


def test_metrics_wired(client: TestClient) -> None:
    client.get("/healthz")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"http_requests_total" in resp.content


def test_correlation_id_echoed(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"X-Correlation-ID": "trace-1"})
    assert resp.headers["X-Correlation-ID"] == "trace-1"
