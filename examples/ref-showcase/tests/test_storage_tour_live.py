"""Live storage-tour test — real MinIO round-trip. Skips when STORAGE_* is absent.

Proves the parts of the Storage surface the endpoints lean on against a real S3-compatible store:
``put_stream`` (multipart upload), ``exists``/``get_bytes``, prefix ``list``, presigned URL minting,
and ``delete``. Like the full-pipeline test, this skips under `check-live` (ephemeral PG, no MinIO)
and runs against the full stack when STORAGE_* is in the environment.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ref_showcase.resources import Resources

pytestmark = pytest.mark.live


def test_storage_roundtrip(live_resources: Resources) -> None:
    res = live_resources
    if res.storage is None:
        pytest.skip("storage tour needs MinIO (STORAGE_* env) — run against the full stack")
    s = res.storage
    key = "uploads/live-stream-test.txt"

    s.put_stream(key, io.BytesIO(b"hello via stream"), content_type="text/plain")
    try:
        assert s.exists(key)
        assert s.get_bytes(key) == b"hello via stream"

        listed = [o.key for o in s.list("uploads/", limit=100)]
        assert key in listed

        assert s.presigned_get_url(key, expires_in=120).startswith("http")
        assert s.presigned_put_url(key, expires_in=120).startswith("http")
    finally:
        s.delete(key)
    assert not s.exists(key)  # delete removed it
