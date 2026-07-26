"""Tests for mini_cloud.storage.

Unit tests validate construction/config wiring without a network. Live tests (marked ``live``,
skipped unless ``--run-live`` + ``STORAGE_*`` env) exercise a real MinIO/S3 round-trip.
"""

from __future__ import annotations

import dataclasses
import os
import uuid

import pytest
from mini_cloud.config import MissingConfigError, load_settings

from mini_cloud.storage import Storage, StorageError


def test_requires_endpoint() -> None:
    with pytest.raises(StorageError, match="STORAGE_ENDPOINT"):
        Storage(endpoint_url="", access_key="a", secret_key="b", bucket="x")


def test_requires_bucket() -> None:
    with pytest.raises(StorageError, match="STORAGE_BUCKET"):
        Storage(endpoint_url="http://x:9000", access_key="a", secret_key="b", bucket="")


def test_from_settings_wires_bucket() -> None:
    s = load_settings(
        environ={
            "STORAGE_ENDPOINT": "http://127.0.0.1:9000",
            "STORAGE_ACCESS_KEY": "minioadmin",
            "STORAGE_SECRET_KEY": "minioadmin",
            "STORAGE_BUCKET": "demo-x",
        }
    )
    store = Storage.from_settings(s)
    assert store.bucket == "demo-x"


def test_from_settings_missing_keys_fails_fast() -> None:
    s = load_settings(environ={"STORAGE_ENDPOINT": "http://127.0.0.1:9000"})
    with pytest.raises(MissingConfigError, match="STORAGE_ACCESS_KEY"):
        Storage.from_settings(s)


@pytest.mark.live
def test_round_trip() -> None:
    if not os.environ.get("STORAGE_ENDPOINT"):
        pytest.skip("no STORAGE_* env")
    settings = dataclasses.replace(load_settings(), storage_bucket=f"itest-{uuid.uuid4().hex[:8]}")
    store = Storage.from_settings(settings)
    store.ensure_bucket()
    store.put_bytes("a/b.txt", b"hello", content_type="text/plain")
    assert store.exists("a/b.txt")
    assert store.get_bytes("a/b.txt") == b"hello"
    keys = [o.key for o in store.list("a/")]
    assert "a/b.txt" in keys
    url = store.presigned_get_url("a/b.txt", expires_in=60)
    assert "a/b.txt" in url
    store.delete("a/b.txt")
    assert not store.exists("a/b.txt")
    with pytest.raises(KeyError):
        store.get_bytes("a/b.txt")
