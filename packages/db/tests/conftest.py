"""Test fixtures for mini_cloud.db.

Live tests need a throwaway Postgres. They run only when ``DATABASE_URL`` is set in the env AND
``--run-live`` is passed, so the default ``pytest`` run (no services) stays green. The scorecard's
``validation_harness`` metric points these at a disposable Postgres, never the always-on stack.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that hit a real Postgres"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") and os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="needs --run-live and DATABASE_URL (throwaway Postgres)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def pg() -> Iterator[object]:
    """A connection to the throwaway Postgres, with the queue schema created and left clean."""
    from mini_cloud.db import JobQueue, connect

    dsn = os.environ["DATABASE_URL"]
    conn = connect(dsn, autocommit=True)
    q = JobQueue(conn)
    q.create_schema()
    conn.execute("DELETE FROM mini_cloud_jobs")
    conn.execute("DELETE FROM mini_cloud_dead_letter")
    try:
        yield conn
    finally:
        conn.execute("DELETE FROM mini_cloud_jobs")
        conn.execute("DELETE FROM mini_cloud_dead_letter")
        conn.close()
