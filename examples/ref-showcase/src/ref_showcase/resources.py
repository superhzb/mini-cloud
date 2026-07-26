"""Wire the SDK packages into one ``Resources`` object the app, worker, and seed share.

Extends ``ref-fastapi``'s pattern with the showcase's richer surface: a relational
:class:`DocumentRepository`, **three fan-out queues** (``ingest`` → ``embed`` + ``summarize``)
plus two demo queues (``long`` for heartbeat, ``poison`` for dead-letter), and an inference client
whose absence degrades to deterministic offline fallbacks so the pipeline still runs.

Everything is constructed from canonical :class:`~mini_cloud.config.Settings`. Each resource is
optional so the process still boots (and answers ``/healthz``) when a backing service is absent;
``/readyz`` reports what's actually reachable. Repoint env and the same wiring targets a VPS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from mini_cloud.analytics import Analytics, migrations_path
from mini_cloud.config import Settings, load_settings
from mini_cloud.db import ConnSource, JobQueue, make_pool, migrate
from mini_cloud.inference import InferenceClient
from mini_cloud.obs import get_logger
from mini_cloud.storage import Storage

from .domain import DocumentRepository

MIGRATIONS_DIR = "migrations"

# Three real pipeline queues (fan-out: ingest enqueues embed + summarize) plus two demo queues the
# queue tour drives directly.
INGEST_QUEUE = "ingest"
EMBED_QUEUE = "embed"
SUMMARIZE_QUEUE = "summarize"
LONG_QUEUE = "long"  # a long job that heartbeats via JobQueue.extend()
POISON_QUEUE = "poison"  # a job that always fails -> backoff -> dead-letter -> requeue
PIPELINE_QUEUES: tuple[str, ...] = (
    INGEST_QUEUE,
    EMBED_QUEUE,
    SUMMARIZE_QUEUE,
)
WORK_QUEUES: tuple[str, ...] = (
    *PIPELINE_QUEUES,
    LONG_QUEUE,
    POISON_QUEUE,
)

_log = get_logger("ref_showcase.resources")


@dataclass(slots=True)
class Resources:
    """The app's live dependencies. Fields are ``None`` when their env isn't configured."""

    settings: Settings
    pool: ConnSource | None = None
    repo: DocumentRepository | None = None
    queue: JobQueue | None = None
    storage: Storage | None = None
    inference: InferenceClient | None = None
    # Product analytics rides a SEPARATE Postgres DB (MINI_ANALYTICS_DSN), so it carries its own
    # pool alongside the app's own `pool`. Both are None when their env isn't configured.
    analytics: Analytics | None = None
    analytics_pool: ConnSource | None = None

    def require_repo(self) -> DocumentRepository:
        if self.repo is None:
            raise RuntimeError("database unavailable — set DATABASE_URL")
        return self.repo

    def require_queue(self) -> JobQueue:
        if self.queue is None:
            raise RuntimeError("job queue unavailable — set DATABASE_URL")
        return self.queue

    def require_storage(self) -> Storage:
        if self.storage is None:
            raise RuntimeError("storage unavailable — set STORAGE_* env")
        return self.storage

    def require_inference(self) -> InferenceClient:
        if self.inference is None:
            raise RuntimeError("inference unavailable — set MINI_INFERENCE_URL")
        return self.inference

    def require_analytics(self) -> Analytics:
        if self.analytics is None:
            raise RuntimeError("analytics unavailable — set MINI_ANALYTICS_DSN")
        return self.analytics


def build_resources(settings: Settings | None = None) -> Resources:
    """Construct whatever the environment provides. Idempotent bootstrap (migrate + queue schema
    + ensure bucket) runs here so a fresh clone comes up ready in one step."""
    settings = settings or load_settings()
    res = Resources(settings=settings)

    if settings.database_url:
        pool = make_pool(settings.database_url)
        res.pool = pool
        applied = migrate(pool, MIGRATIONS_DIR)  # apply this app's own ordered schema
        queue = JobQueue(pool)
        queue.create_schema()  # SDK queue tables (idempotent)
        res.queue = queue
        res.repo = DocumentRepository(pool)
        _log.info("db ready", extra={"migrations_dir": MIGRATIONS_DIR, "applied": applied})

    if settings.storage_endpoint and settings.storage_bucket:
        storage = Storage.from_settings(settings)
        storage.ensure_bucket()
        res.storage = storage
        _log.info("storage ready", extra={"bucket": settings.storage_bucket})

    if settings.inference_url:
        # INFERENCE_MODEL / INFERENCE_EMBED_MODEL are this app's own (non-canonical) config — apps
        # own their extra env beyond the SDK's canonical names.
        res.inference = InferenceClient.from_settings(
            settings, default_model=os.environ.get("INFERENCE_MODEL")
        )
        _log.info("inference ready", extra={"url": settings.inference_url})

    if settings.analytics_dsn:
        # Product analytics is the first package that ships and applies its OWN migrations, against
        # a SEPARATE DSN from the app's own DB — the new pattern from docs/analytics-plan.md.
        analytics_pool = make_pool(settings.analytics_dsn)
        migrate(analytics_pool, migrations_path())  # package-owned event-store schema
        res.analytics_pool = analytics_pool
        res.analytics = Analytics.from_settings(settings, source=analytics_pool)
        _log.info(
            "analytics ready",
            extra={"project": res.analytics.project, "backend": settings.analytics_backend},
        )

    return res


def embed_model() -> str | None:
    """The app's chosen embedding model, if configured (non-canonical app env)."""
    return os.environ.get("INFERENCE_EMBED_MODEL")
