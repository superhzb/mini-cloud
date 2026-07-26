"""Wire the SDK packages into one ``Resources`` object the app and worker share.

Everything comes from canonical :class:`~mini_cloud.config.Settings`. Each resource is optional so
the process still boots (and answers ``/healthz``) when a backing service is absent; ``/readyz``
reports actual reachability. This is the seam you graduate on: repoint env, same wiring.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mini_cloud.config import Settings, load_settings
from mini_cloud.db import JobQueue, make_pool, migrate
from mini_cloud.db.connection import ConnSource
from mini_cloud.inference import InferenceClient
from mini_cloud.obs import get_logger
from mini_cloud.storage import Storage

MIGRATIONS_DIR = "migrations"
WORK_QUEUE = "{{name}}"

_log = get_logger("{{package}}.resources")


@dataclass(slots=True)
class Resources:
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
    """Construct whatever the environment provides; run idempotent bootstrap (migrate, queue
    schema, ensure bucket) so a fresh clone comes up ready."""
    settings = settings or load_settings()
    res = Resources(settings=settings)

    if settings.database_url:
        pool = make_pool(settings.database_url)
        res.pool = pool
        migrate(pool, MIGRATIONS_DIR)
        queue = JobQueue(pool)
        queue.create_schema()
        res.queue = queue
        _log.info("db ready")

    if settings.storage_endpoint and settings.storage_bucket:
        storage = Storage.from_settings(settings)
        storage.ensure_bucket()
        res.storage = storage
        _log.info("storage ready", extra={"bucket": settings.storage_bucket})

    if settings.inference_url:
        res.inference = InferenceClient.from_settings(
            settings, default_model=os.environ.get("INFERENCE_MODEL")
        )
        _log.info("inference ready")

    return res
