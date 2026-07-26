"""Test fixtures for ref-showcase. Live tests need the real infra stack (Postgres, and MinIO for
the storage-threaded pipeline tests) and are gated behind --run-live + canonical env, so
`make check`/`make test` stay green with no services running."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that hit real infra"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") and os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="needs --run-live and canonical infra env")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def live_resources() -> Iterator[object]:
    """Resources wired straight from PROCESS env (never ./.env), with clean tables.

    Decoupling from ``./.env`` is deliberate: ``check-live`` exports only ``DATABASE_URL`` (an
    ephemeral throwaway Postgres), so the db + queue tours run without requiring MinIO. Storage is
    attached only when ``STORAGE_*`` is present in the environment (a full-stack run), which is
    what the full-pipeline test keys off of.
    """
    from mini_cloud.config import load_settings
    from mini_cloud.db import JobQueue, make_pool, migrate
    from mini_cloud.storage import Storage

    from ref_showcase.domain import DocumentRepository
    from ref_showcase.resources import MIGRATIONS_DIR, Resources

    settings = load_settings(dotenv=None)  # process env only — no ./.env coupling
    pool = make_pool(os.environ["DATABASE_URL"])
    migrate(pool, MIGRATIONS_DIR)
    queue = JobQueue(pool)
    queue.create_schema()
    res = Resources(settings=settings, pool=pool, queue=queue, repo=DocumentRepository(pool))

    if settings.storage_endpoint and settings.storage_bucket:
        storage = Storage.from_settings(settings)
        storage.ensure_bucket()
        res.storage = storage

    with pool.connection() as conn:
        conn.execute(
            "TRUNCATE documents, tags, mini_cloud_jobs, mini_cloud_dead_letter "
            "RESTART IDENTITY CASCADE"
        )
        conn.commit()

    try:
        yield res
    finally:
        pool.close()


@pytest.fixture
def analytics_pool() -> Iterator[object]:
    """A pool to the analytics event store with a clean, migrated schema.

    Gated on ``MINI_ANALYTICS_DSN`` (separate from ``DATABASE_URL``): ``check-live`` points it at
    the same throwaway Postgres, so the analytics tour runs there without a second container. Skips
    when unset, exactly like the storage-dependent tests skip without MinIO.
    """
    dsn = os.environ.get("MINI_ANALYTICS_DSN")
    if not dsn:
        pytest.skip("analytics live tests need MINI_ANALYTICS_DSN")

    from mini_cloud.analytics import migrations_path
    from mini_cloud.db import make_pool, migrate

    pool = make_pool(dsn)
    migrate(pool, migrations_path())
    with pool.connection() as conn:
        conn.execute(
            "TRUNCATE analytics_events, analytics_persons, analytics_person_aliases "
            "RESTART IDENTITY"
        )
        conn.commit()

    try:
        yield pool
    finally:
        pool.close()
