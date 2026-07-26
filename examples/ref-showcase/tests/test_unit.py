"""Pure unit tests — no services. Cover the pipeline's deterministic seams and app boot behaviour.

The exhaustive `__all__` coverage-gate test (asserting every public SDK symbol is exercised in
src/) lands with the seed corpus in a later build step; these cover the logic reachable offline.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ref_showcase.app import create_app
from ref_showcase.pipeline import (
    HANDLERS,
    chunk_text,
    dispatch,
    fallback_embedding,
    fallback_summary,
)
from ref_showcase.resources import WORK_QUEUES


# --- pure pipeline helpers ----------------------------------------------------------
def test_chunk_text_keeps_short_paragraphs_whole() -> None:
    chunks = chunk_text("First para.\nSecond para.\n\n  \nThird.")
    assert chunks == ["First para.", "Second para.", "Third."]


def test_chunk_text_splits_long_paragraph_on_word_boundaries() -> None:
    para = " ".join(["word"] * 200)  # ~1000 chars, one paragraph
    chunks = chunk_text(para, max_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)
    # No word is lost or split mid-token.
    assert " ".join(chunks).split() == para.split()


def test_chunk_text_empty_is_empty() -> None:
    assert chunk_text("   \n  \n") == []


def test_fallback_embedding_is_deterministic_and_unit_norm() -> None:
    a = fallback_embedding("the quick brown fox")
    b = fallback_embedding("the quick brown fox")
    assert a == b  # deterministic — reproducible search
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-9)
    assert fallback_embedding("totally different text") != a


def test_fallback_summary_takes_first_nonempty_line() -> None:
    assert fallback_summary("\n\n  First line.\nSecond line.") == "First line."


def test_every_work_queue_has_a_handler() -> None:
    # The worker round-robins WORK_QUEUES through dispatch(); each must resolve to a handler.
    assert set(WORK_QUEUES) == set(HANDLERS)


def test_dispatch_rejects_unknown_queue() -> None:
    from mini_cloud.db import Job

    job = Job(
        id=1,
        queue="does-not-exist",
        payload={"correlation_id": "c1"},
        priority=0,
        attempts=1,
        max_attempts=5,
        dedupe_key=None,
        created_at=_now(),
    )
    # dispatch resolves the handler before touching Resources, so a bare object suffices.
    with pytest.raises(RuntimeError, match="no handler registered"):
        dispatch(object(), job)  # type: ignore[arg-type]


def _now():  # noqa: ANN202 — tiny test helper
    from datetime import UTC, datetime

    return datetime(2026, 7, 25, tzinfo=UTC)


# --- app boots with no services -----------------------------------------------------
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> Iterator[TestClient]:
    for var in (
        "DATABASE_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "MINI_INFERENCE_URL",
        "MINI_INFERENCE_PROJECT",
        "LOKI_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_NAME", "ref-showcase-test")
    # Run from a clean dir so load_settings() doesn't pick up a real ./.env pointing at services
    # this no-services test can't reach (keeps `make check` green with the stack down).
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]  # tmp_path is a Path
    with TestClient(create_app()) as c:
        yield c


def test_healthz_is_liveness_only(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_not_ready_without_services(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


def test_metrics_endpoint_wired(client: TestClient) -> None:
    client.get("/healthz")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"http_requests_total" in resp.content


def test_correlation_id_echoed(client: TestClient) -> None:
    resp = client.get("/healthz", headers={"X-Correlation-ID": "trace-9"})
    assert resp.headers["X-Correlation-ID"] == "trace-9"


def test_debug_config_redacts_secrets_and_covers_inference_project(
    client: TestClient,
) -> None:
    resp = client.get("/debug/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "MINI_INFERENCE_PROJECT" in body["canonical_env"]
    assert body["inference_project"] == "ref-showcase-test"
    assert body["settings"]["storage_secret_key"] in (None, "<redacted>")


def test_debug_obs_reports_request_correlation_and_custom_metrics(client: TestClient) -> None:
    resp = client.get("/debug/obs", headers={"X-Correlation-ID": "obs-tour-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["correlation_id"] == "obs-tour-1"
    assert set(body["custom_collectors"]) == {
        "documents_ingested_total",
        "search_latency_seconds",
        "queue_jobs_processed_total",
    }


def test_documents_requires_storage(client: TestClient) -> None:
    with pytest.raises(RuntimeError, match="storage unavailable"):
        client.post("/documents", json={"title": "t", "text": "hi"})
