"""Unit tests for the storage tour — endpoints exercised against an in-memory fake Storage.

No MinIO: a FakeStorage records puts and answers reads so `make check` stays fully offline. The
real client round-trip (put_stream/presigned/list against MinIO) is proven in the live test.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient
from mini_cloud.storage import ObjectInfo


class FakeStorage:
    """In-memory stand-in for mini_cloud.storage.Storage — the methods the tour endpoints call."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_stream(self, key: str, fileobj: BinaryIO, *, content_type: str | None = None) -> None:
        self.objects[key] = fileobj.read()

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def exists(self, key: str) -> bool:
        return key in self.objects

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list(self, prefix: str = "", *, limit: int | None = None) -> Iterator[ObjectInfo]:
        n = 0
        for k, v in self.objects.items():
            if not k.startswith(prefix):
                continue
            yield ObjectInfo(key=k, size=len(v), last_modified="2026-07-25T00:00:00Z")
            n += 1
            if limit is not None and n >= limit:
                return

    def presigned_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        return f"http://minio.local/{key}?get&e={expires_in}"

    def presigned_put_url(self, key: str, *, expires_in: int = 3600) -> str:
        return f"http://minio.local/{key}?put&e={expires_in}"

    def bucket_exists(self) -> bool:
        return True


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, storage: FakeStorage
) -> Iterator[TestClient]:
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
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]

    def fake_build(settings: object = None) -> Resources:
        return Resources(settings=settings or load_settings(dotenv=None), storage=storage)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "build_resources", fake_build)
    with TestClient(app_mod.create_app()) as c:
        yield c


def test_upload_streams_to_storage_and_returns_presigned_url(
    client: TestClient, storage: FakeStorage
) -> None:
    resp = client.post(
        "/storage/uploads",
        files={"file": ("notes.txt", b"hello stream", "text/plain")},
        data={"prefix": "uploads/"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["key"] == "uploads/notes.txt"
    assert body["get_url"].startswith("http")
    assert storage.objects["uploads/notes.txt"] == b"hello stream"


def test_list_objects_filters_by_prefix_and_limit(client: TestClient, storage: FakeStorage) -> None:
    storage.objects.update({"docs/a.txt": b"a", "docs/b.txt": b"bb", "chunks/1.txt": b"c"})
    resp = client.get("/storage/objects", params={"prefix": "docs/", "limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert {i["key"] for i in body["items"]} == {"docs/a.txt", "docs/b.txt"}


def test_download_proxies_bytes_and_404s_when_absent(
    client: TestClient, storage: FakeStorage
) -> None:
    storage.objects["summaries/9.txt"] = b"a summary"
    ok = client.get("/storage/object/content", params={"key": "summaries/9.txt"})
    assert ok.status_code == 200
    assert ok.content == b"a summary"

    missing = client.get("/storage/object/content", params={"key": "nope"})
    assert missing.status_code == 404


@pytest.mark.parametrize(("method", "marker"), [("get", "?get"), ("put", "?put")])
def test_presign_mints_get_and_put_urls(client: TestClient, method: str, marker: str) -> None:
    resp = client.post(
        "/storage/presign", json={"key": "docs/x.txt", "method": method, "expires_in": 120}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == method
    assert marker in body["url"]
    assert body["expires_in"] == 120


def test_delete_removes_object(client: TestClient, storage: FakeStorage) -> None:
    storage.objects["uploads/gone.txt"] = b"bye"
    resp = client.request("DELETE", "/storage/object", params={"key": "uploads/gone.txt"})
    assert resp.status_code == 204
    assert "uploads/gone.txt" not in storage.objects
