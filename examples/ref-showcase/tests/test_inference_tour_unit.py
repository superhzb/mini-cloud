"""Unit tests for the inference tour — endpoints exercised with a mocked InferenceClient.

The plan makes the AI routes live-required at runtime, but `make check` must stay offline, so the
gateway is faked: chat/models/streaming return canned data, and the live-required routes are also
asserted to 503 when no gateway is configured. Semantic search runs through its offline fallback.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ref_showcase.domain import ChunkRow, DocumentDetail, DocumentSummary
from ref_showcase.pipeline import fallback_embedding


# --- fakes --------------------------------------------------------------------------
def _detail(document_id: int, title: str, chunk_texts: list[str]) -> DocumentDetail:
    summary = DocumentSummary(
        id=document_id,
        doc_key=f"docs/{document_id}.txt",
        title=title,
        source="test",
        status="ready",
        created_at=datetime(2026, 7, 25, tzinfo=UTC),
        summary_key=None,
        chunk_count=len(chunk_texts),
        tags=[],
    )
    chunks = [
        ChunkRow(id=i, ordinal=i, content=t, chunk_key=None, has_embedding=True)
        for i, t in enumerate(chunk_texts)
    ]
    return DocumentDetail(summary=summary, chunks=chunks)


class FakeRepo:
    def __init__(self, docs: dict[int, DocumentDetail]) -> None:
        self._docs = docs

    def get_document(self, document_id: int) -> DocumentDetail | None:
        return self._docs.get(document_id)

    def iter_embedded_chunks(self) -> list[tuple[int, int, list[float]]]:
        out: list[tuple[int, int, list[float]]] = []
        for doc_id, detail in self._docs.items():
            for c in detail.chunks:
                out.append((c.id, doc_id, fallback_embedding(c.content)))
        return out


def _stream_chunks() -> Iterator[object]:
    for piece in ("Hello", " world", None):  # None delta => skipped, like a real finish chunk
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])


class FakeInference:
    default_model = "fake-model"

    def __init__(self) -> None:
        completions = SimpleNamespace(create=self._create)
        self.openai = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def chat_messages(self, messages: object, *, max_tokens: int | None = None, **_: object) -> str:
        return "FAKE REPLY"

    def embed(self, text: str, *, model: str | None = None, **_: object) -> list[list[float]]:
        return [fallback_embedding(text)]

    def models(self) -> list[str]:
        return ["fake-chat", "fake-embed"]

    def _create(self, *, stream: bool = False, **_: object) -> Iterator[object]:
        assert stream is True
        return _stream_chunks()


# --- fixtures -----------------------------------------------------------------------
def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    *,
    repo: object,
    inference: object,
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
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("INFERENCE_EMBED_MODEL", raising=False)  # force the offline embed fallback
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]

    def fake_build(settings: object = None) -> Resources:
        return Resources(
            settings=settings or load_settings(dotenv=None),  # type: ignore[arg-type]
            repo=repo,  # type: ignore[arg-type]
            inference=inference,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(app_mod, "build_resources", fake_build)
    client = TestClient(app_mod.create_app())
    client.__enter__()
    return client


@pytest.fixture
def corpus() -> FakeRepo:
    return FakeRepo(
        {
            1: _detail(1, "Alpha doc", ["alpha beta gamma", "alpha beta delta"]),
            2: _detail(2, "Zeta doc", ["zeta eta theta"]),
        }
    )


# --- search (works offline via the fallback embedder) -------------------------------
def test_search_ranks_and_joins_titles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, corpus: FakeRepo
) -> None:
    client = _make_client(monkeypatch, tmp_path, repo=corpus, inference=None)
    try:
        resp = client.post("/search", json={"query": "alpha beta gamma", "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 1
        top = body["hits"][0]
        assert top["document_id"] == 1
        assert top["title"] == "Alpha doc"
        assert top["score"] == pytest.approx(1.0)
    finally:
        client.__exit__(None, None, None)


# --- chat / models / streaming (mocked gateway) -------------------------------------
def test_chat_over_document_uses_chat_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, corpus: FakeRepo
) -> None:
    client = _make_client(monkeypatch, tmp_path, repo=corpus, inference=FakeInference())
    try:
        resp = client.post(
            "/documents/1/chat",
            json={"messages": [{"role": "user", "content": "what is this about?"}]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "FAKE REPLY"
        assert body["turns"] == 1
    finally:
        client.__exit__(None, None, None)


def test_chat_404_for_unknown_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, corpus: FakeRepo
) -> None:
    client = _make_client(monkeypatch, tmp_path, repo=corpus, inference=FakeInference())
    try:
        resp = client.post("/documents/999/chat", json={"messages": [{"content": "hi"}]})
        assert resp.status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_lists_gateway_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, corpus: FakeRepo
) -> None:
    client = _make_client(monkeypatch, tmp_path, repo=corpus, inference=FakeInference())
    try:
        resp = client.get("/inference/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == ["fake-chat", "fake-embed"]
    finally:
        client.__exit__(None, None, None)


def test_summary_stream_emits_sse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, corpus: FakeRepo
) -> None:
    client = _make_client(monkeypatch, tmp_path, repo=corpus, inference=FakeInference())
    try:
        resp = client.get("/documents/1/summary/stream")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "data: Hello" in resp.text
        assert "data:  world" in resp.text
        assert "data: [DONE]" in resp.text
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("path", ["/inference/models"])
def test_ai_routes_503_without_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, corpus: FakeRepo, path: str
) -> None:
    client = _make_client(monkeypatch, tmp_path, repo=corpus, inference=None)
    try:
        assert client.get(path).status_code == 503
        chat = client.post("/documents/1/chat", json={"messages": [{"content": "hi"}]})
        assert chat.status_code == 503
        assert client.get("/documents/1/summary/stream").status_code == 503
    finally:
        client.__exit__(None, None, None)
