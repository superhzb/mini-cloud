"""Offline unit coverage for bounded resource-aware seeding and its HTTP endpoint."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import ref_showcase.app as app_mod
import ref_showcase.seed as seed_mod
from ref_showcase.resources import PIPELINE_QUEUES, Resources
from ref_showcase.seed import SeedResult


class FakeRepo:
    def __init__(self) -> None:
        self.titles: list[str] = []

    def list_documents(self, *, limit: int, offset: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(title=title, source="seed")
            for title in self.titles[offset : offset + limit]
        ]


def resources(repo: FakeRepo | None = None) -> Resources:
    return Resources(
        settings=cast(Any, object()),
        repo=cast(Any, repo or FakeRepo()),
        queue=cast(Any, object()),
        storage=cast(Any, object()),
    )


def test_seed_corpus_is_bounded_idempotent_and_drains_only_pipeline_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = FakeRepo()
    res = resources(repo)
    gateway = object()
    res.inference = cast(Any, gateway)
    drained: list[tuple[str, ...]] = []

    def fake_submit(_res: Resources, **kwargs: object) -> tuple[int, str]:
        repo.titles.append(cast(str, kwargs["title"]))
        return len(repo.titles), cast(str, kwargs["doc_key"])

    def fake_drain(_res: Resources, queues: tuple[str, ...]) -> int:
        drained.append(queues)
        return len(repo.titles) * 3

    monkeypatch.setattr(seed_mod, "submit_document", fake_submit)
    monkeypatch.setattr(seed_mod, "_drain_pipeline", fake_drain)

    first = seed_mod.seed_corpus(res, count=3)
    second = seed_mod.seed_corpus(res, count=3)

    assert (first.requested, first.created, first.skipped) == (3, 3, 0)
    assert (second.requested, second.created, second.skipped) == (3, 0, 3)
    assert drained == [PIPELINE_QUEUES, PIPELINE_QUEUES]
    assert "long" not in drained[0] and "poison" not in drained[0]
    assert first.analytics == "unavailable" and first.analytics_events == 0
    assert first.mode == "offline-fallback"
    assert res.inference is gateway


def test_seed_corpus_refreshes_analytics_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    res = resources()
    res.analytics_pool = cast(Any, object())
    res.analytics = cast(Any, SimpleNamespace(project="console-test"))
    monkeypatch.setattr(seed_mod, "submit_document", lambda *_args, **_kwargs: (1, "key"))
    monkeypatch.setattr(seed_mod, "_drain_pipeline", lambda *_args, **_kwargs: 3)
    seen: list[tuple[object, str]] = []

    def fake_analytics(pool: object, project: str) -> int:
        seen.append((pool, project))
        return 87

    monkeypatch.setattr(seed_mod, "seed_analytics_events", fake_analytics)
    result = seed_mod.seed_corpus(res, count=1)
    assert result.analytics == "seeded"
    assert result.analytics_events == 87
    assert seen == [(res.analytics_pool, "console-test")]


@pytest.fixture
def no_service_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    for variable in (
        "DATABASE_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "MINI_INFERENCE_URL",
        "MINI_ANALYTICS_DSN",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(app_mod.create_app()) as client:
        yield client


def test_seed_endpoint_defaults_bounds_and_missing_dependencies(
    no_service_client: TestClient,
) -> None:
    missing = no_service_client.post("/showcase/seed")
    assert missing.status_code == 503
    assert "database unavailable" in missing.json()["detail"]
    assert no_service_client.post("/showcase/seed?count=0").status_code == 422
    assert no_service_client.post("/showcase/seed?count=13").status_code == 422


def test_seed_endpoint_uses_injected_resources_and_default_count(
    no_service_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    injected = resources()
    no_service_client.app.state.resources = injected
    seen: list[tuple[Resources, int, bool]] = []

    def fake_seed(res: Resources, *, count: int, live: bool) -> SeedResult:
        seen.append((res, count, live))
        return SeedResult(count, count, 0, count * 3, 0, "unavailable", "offline-fallback")

    monkeypatch.setattr(app_mod, "seed_corpus", fake_seed)
    response = no_service_client.post("/showcase/seed")
    assert response.status_code == 200
    assert response.json()["requested"] == 6
    assert seen == [(injected, 6, False)]


def test_seed_endpoint_rejects_a_concurrent_attempt(no_service_client: TestClient) -> None:
    lock = no_service_client.app.state.seed_lock
    assert lock.acquire(blocking=False)
    try:
        response = no_service_client.post("/showcase/seed")
    finally:
        lock.release()
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
