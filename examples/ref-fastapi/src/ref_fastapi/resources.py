"""Wire the SDK packages into a single ``Resources`` object the app and worker share.

Everything is constructed from canonical :class:`~mini_cloud.config.Settings`. Each resource is
optional at construction time so the process can still boot (and answer ``/healthz``) when a
backing service is absent — ``/readyz`` is what reports whether the dependencies are actually
reachable. This is the seam an app graduates on: repoint env, and the same wiring targets a VPS.
"""

from __future__ import annotations

from dataclasses import dataclass

from mini_cloud.config import Settings, load_settings
from mini_cloud.db import JobQueue, make_pool, migrate
from mini_cloud.db.connection import ConnSource
from mini_cloud.inference import InferenceClient
from mini_cloud.obs import get_logger
from mini_cloud.storage import Storage

MIGRATIONS_DIR = "migrations"
SUMMARIZE_QUEUE = "summarize"

_log = get_logger("ref_fastapi.resources")


@dataclass(slots=True)
class Resources:
    """The app's live dependencies. Fields are ``None`` when their env isn't configured."""

    settings: Settings
    pool: ConnSource | None = None
    queue: JobQueue | None = None
    storage: Storage | None = None
    inference: InferenceClient | None = None

    def require_queue(self) -> JobQueue:
        if self.queue is None:
            raise RuntimeError("job queue unavailable — set DATABASE_URL")
        return self.queue

    def require_storage(self) -> Storage:
        if self.storage is None:
            raise RuntimeError("storage unavailable — set STORAGE_* env")
        return self.storage


def build_resources(settings: Settings | None = None) -> Resources:
    """Construct whatever the environment provides. Idempotent bootstrap (migrate + create schema
    + ensure bucket) runs here so a fresh clone comes up ready in one step."""
    settings = settings or load_settings()
    res = Resources(settings=settings)

    if settings.database_url:
        pool = make_pool(settings.database_url)
        res.pool = pool
        migrate(pool, MIGRATIONS_DIR)  # apply this app's own schema
        queue = JobQueue(pool)
        queue.create_schema()  # queue tables (idempotent)
        res.queue = queue
        _log.info("db ready", extra={"migrations_dir": MIGRATIONS_DIR})

    if settings.storage_endpoint and settings.storage_bucket:
        storage = Storage.from_settings(settings)
        storage.ensure_bucket()
        res.storage = storage
        _log.info("storage ready", extra={"bucket": settings.storage_bucket})

    if settings.inference_url:
        # INFERENCE_MODEL is this app's own (non-canonical) config — apps own their extra env.
        import os

        res.inference = InferenceClient.from_settings(
            settings, default_model=os.environ.get("INFERENCE_MODEL")
        )
        _log.info("inference ready", extra={"url": settings.inference_url})

    return res
