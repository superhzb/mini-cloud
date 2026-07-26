"""Unit tests for the analytics tour — no database.

The capture/identify/alias endpoints run against a fake in-process analytics client; the query
endpoints (funnel/retention/events) hit the analytics DB and are covered by the live suite. Also
pins the deterministic seed event stream and the pure query helpers.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ref_showcase.analytics_tour import (
    EVENT_SEARCH_PERFORMED,
    FUNNEL_STEPS,
    resolve_actor,
)
from ref_showcase.seed import ANALYTICS_SEED_USERS, generate_event_stream


# --- fakes --------------------------------------------------------------------------
class FakeAnalytics:
    project = "test-proj"

    def __init__(self) -> None:
        self.captured: list[tuple[str, str, dict[str, Any], str | None]] = []
        self.identified: list[tuple[str, dict[str, Any]]] = []
        self.aliased: list[tuple[str, str]] = []
        self.flushed = 0

    def capture(
        self,
        distinct_id: str,
        event: str,
        properties: Any = None,
        timestamp: Any = None,
        *,
        session_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.captured.append((distinct_id, event, dict(properties or {}), session_id))

    def identify(self, distinct_id: str, properties: Any = None) -> None:
        self.identified.append((distinct_id, dict(properties or {})))

    def alias(self, previous_id: str, distinct_id: str) -> None:
        self.aliased.append((previous_id, distinct_id))

    def flush(self) -> None:
        self.flushed += 1

    def close(self) -> None:
        pass


def _make_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, *, analytics: object, repo: object = None
) -> TestClient:
    from mini_cloud.config import load_settings

    from ref_showcase import app as app_mod
    from ref_showcase.resources import Resources

    for var in (
        "DATABASE_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "MINI_INFERENCE_URL",
        "LOKI_URL",
        "MINI_ANALYTICS_DSN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("INFERENCE_EMBED_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]

    def fake_build(settings: object = None) -> Resources:
        return Resources(
            settings=settings or load_settings(dotenv=None),  # type: ignore[arg-type]
            repo=repo,  # type: ignore[arg-type]
            analytics=analytics,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(app_mod, "build_resources", fake_build)
    client = TestClient(app_mod.create_app())
    client.__enter__()
    return client


# --- endpoints (fake client, no DB) -------------------------------------------------
def test_capture_endpoint_buffers(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    fake = FakeAnalytics()
    client = _make_client(monkeypatch, tmp_path, analytics=fake)
    try:
        resp = client.post(
            "/analytics/capture",
            json={"distinct_id": "u1", "event": "custom", "properties": {"k": 1}},
        )
        assert resp.status_code == 202
        assert fake.captured == [("u1", "custom", {"k": 1}, None)]
    finally:
        client.__exit__(None, None, None)


def test_identify_and_alias_endpoints(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    fake = FakeAnalytics()
    client = _make_client(monkeypatch, tmp_path, analytics=fake)
    try:
        assert (
            client.post(
                "/analytics/identify", json={"distinct_id": "u1", "properties": {"plan": "pro"}}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/analytics/alias", json={"previous_id": "anon-1", "distinct_id": "u1"}
            ).status_code
            == 200
        )
        assert fake.identified == [("u1", {"plan": "pro"})]
        assert fake.aliased == [("anon-1", "u1")]
    finally:
        client.__exit__(None, None, None)


def test_analytics_routes_503_without_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    client = _make_client(monkeypatch, tmp_path, analytics=None)
    try:
        assert (
            client.post("/analytics/capture", json={"distinct_id": "u", "event": "e"}).status_code
            == 503
        )
        assert client.get("/analytics/funnel").status_code == 503
        assert client.get("/analytics/retention").status_code == 503
        assert client.get("/analytics/events").status_code == 503
    finally:
        client.__exit__(None, None, None)


def test_sql_endpoint_is_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    client = _make_client(monkeypatch, tmp_path, analytics=None)
    try:
        body = client.get("/analytics/sql").json()
        assert "FILTER (WHERE event = %s)" in body["funnel_sql"]
        assert "date_trunc('week'" in body["retention_sql"]
        assert body["migrations_dir"].endswith("migrations")
    finally:
        client.__exit__(None, None, None)


def test_search_fires_analytics_event(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    from datetime import UTC, datetime

    from ref_showcase.domain import ChunkRow, DocumentDetail, DocumentSummary
    from ref_showcase.pipeline import fallback_embedding

    class Repo:
        def iter_embedded_chunks(self) -> list[tuple[int, int, list[float]]]:
            return [(1, 1, fallback_embedding("alpha beta"))]

        def get_document(self, document_id: int) -> DocumentDetail:
            return DocumentDetail(
                summary=DocumentSummary(
                    id=1,
                    doc_key="k",
                    title="Doc",
                    source="t",
                    status="ready",
                    created_at=datetime(2026, 7, 25, tzinfo=UTC),
                    summary_key=None,
                    chunk_count=1,
                    tags=[],
                ),
                chunks=[
                    ChunkRow(
                        id=1, ordinal=0, content="alpha beta", chunk_key=None, has_embedding=True
                    )
                ],
            )

    fake = FakeAnalytics()
    client = _make_client(monkeypatch, tmp_path, analytics=fake, repo=Repo())
    try:
        resp = client.post(
            "/search", json={"query": "alpha", "limit": 3}, headers={"X-Distinct-Id": "user-9"}
        )
        assert resp.status_code == 200
        events = [(d, e) for d, e, _, _ in fake.captured]
        assert ("user-9", EVENT_SEARCH_PERFORMED) in events
    finally:
        client.__exit__(None, None, None)


# --- helpers / seed stream (pure) ---------------------------------------------------
def test_resolve_actor_prefers_explicit_then_session_then_anon() -> None:
    assert resolve_actor("u1", "s1") == ("u1", "s1")
    assert resolve_actor(None, "s1") == ("anon-s1", "s1")
    assert resolve_actor(None, None) == ("anonymous", None)


def test_event_stream_is_deterministic_and_covers_the_funnel() -> None:
    a = generate_event_stream("proj")
    b = generate_event_stream("proj")
    events_a, persons_a, aliases_a = a
    assert [(e.event, e.distinct_id, e.timestamp) for e in events_a] == [
        (e.event, e.distinct_id, e.timestamp) for e in b[0]
    ]
    # every funnel step appears, and every event is tagged for idempotent re-seeding
    seen = {e.event for e in events_a}
    assert set(FUNNEL_STEPS) <= seen
    assert all(e.properties.get("seeded") for e in events_a)
    assert all(e.project == "proj" for e in events_a)
    # the anonymous->identified stitch is present
    assert persons_a and aliases_a
    assert all(prev.startswith("anon-") for prev, _ in aliases_a)


def test_event_stream_step_counts_decrease() -> None:
    events, _, _ = generate_event_stream("proj")
    step0 = sum(1 for e in events if e.event == FUNNEL_STEPS[0])
    step3 = sum(1 for e in events if e.event == FUNNEL_STEPS[3])
    assert step0 == ANALYTICS_SEED_USERS  # everyone enters
    assert step3 < step0  # funnel drops off
