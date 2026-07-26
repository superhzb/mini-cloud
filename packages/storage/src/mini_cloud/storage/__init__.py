"""mini_cloud.storage — S3/MinIO object storage bound to one per-project bucket.

Retires the filesystem-as-object-store pattern re-invented 5+ times (``hub-api/.data``,
``srt-api`` JSON files, ``srt-flow`` ``STORAGE_ROOT``, ``mlx-audio``, ``~/Public/*``). The seam is
the plain **S3 API** (``STORAGE_ENDPOINT`` + access keys), so MinIO locally and a managed S3 on a
VPS are the same code — only env changes.

    from mini_cloud.config import load_settings
    from mini_cloud.storage import Storage

    store = Storage.from_settings(load_settings())
    store.ensure_bucket()
    store.put_bytes("reports/2026.json", b"{...}", content_type="application/json")
    data = store.get_bytes("reports/2026.json")
    url = store.presigned_get_url("reports/2026.json", expires_in=3600)

Every method operates within the single bucket the app was configured with (``STORAGE_BUCKET``).
Keys are object keys *within* that bucket; there is no cross-bucket surface by design.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mini_cloud.config import Settings

__version__ = "0.1.0"

__all__ = ["Storage", "ObjectInfo", "StorageError"]


class StorageError(RuntimeError):
    """Raised for storage misconfiguration (e.g. missing endpoint/bucket)."""


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Lightweight listing entry."""

    key: str
    size: int
    last_modified: Any  # datetime; boto3 returns tz-aware


class Storage:
    """A client scoped to one bucket. Build via :meth:`from_settings` (canonical) or directly."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        if not endpoint_url:
            raise StorageError("STORAGE_ENDPOINT is required")
        if not bucket:
            raise StorageError("STORAGE_BUCKET is required")
        self.bucket = bucket
        # Path-style addressing: MinIO doesn't do virtual-host buckets by default, and it's the
        # portable choice against S3-compatibles. Signature v4 for presigned URLs.
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Storage:
        """Build from canonical :class:`~mini_cloud.config.Settings`. Fails fast (naming the env
        var) if endpoint/bucket/keys are unset."""
        return cls(
            endpoint_url=settings.require("storage_endpoint"),
            access_key=settings.require("storage_access_key"),
            secret_key=settings.require("storage_secret_key"),
            bucket=settings.require("storage_bucket"),
            region=settings.storage_region,
        )

    # --- bucket lifecycle -----------------------------------------------------------
    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist. Idempotent; safe at boot."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchBucket", "NotFound"):
                self._client.create_bucket(Bucket=self.bucket)
            else:
                raise

    def bucket_exists(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except ClientError:
            return False

    # --- objects --------------------------------------------------------------------
    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        """Store ``data`` under ``key``."""
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def put_stream(self, key: str, fileobj: BinaryIO, *, content_type: str | None = None) -> None:
        """Upload a file-like object (multipart under the hood for large files)."""
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=extra or None)

    def get_bytes(self, key: str) -> bytes:
        """Fetch an object's bytes. Raises :class:`KeyError` if the key is absent."""
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") in ("NoSuchKey", "404"):
                raise KeyError(key) from exc
            raise
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        """Delete an object. No-op if already absent (S3 semantics)."""
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def list(self, prefix: str = "", *, limit: int | None = None) -> Iterator[ObjectInfo]:
        """Yield objects under ``prefix`` (paginated transparently)."""
        paginator = self._client.get_paginator("list_objects_v2")
        yielded = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key")
                if key is None:
                    continue
                yield ObjectInfo(
                    key=key, size=int(obj.get("Size", 0)), last_modified=obj.get("LastModified")
                )
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    # --- presigned URLs -------------------------------------------------------------
    def presigned_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        """A time-limited URL a browser/client can GET directly, without app-side proxying."""
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )

    def presigned_put_url(self, key: str, *, expires_in: int = 3600) -> str:
        """A time-limited URL a client can PUT to (direct upload, bypassing the app)."""
        return self._client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )
