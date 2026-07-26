"""Pure unit tests for mini_cloud.db — no Postgres required."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mini_cloud.db import default_backoff, discover
from mini_cloud.db.queue import _row_to_job


def test_backoff_is_exponential_and_capped() -> None:
    assert default_backoff(1, base_seconds=5) == 5  # first retry waits base
    assert default_backoff(2, base_seconds=5) == 10
    assert default_backoff(3, base_seconds=5) == 20
    assert default_backoff(4, base_seconds=5) == 40
    assert default_backoff(100, base_seconds=5, cap_seconds=60) == 60  # capped


def test_backoff_floor_at_zero_attempts() -> None:
    assert default_backoff(0, base_seconds=5) == 5  # attempts clamped to >=0 exponent


def test_discover_orders_by_numeric_prefix(tmp_path) -> None:
    (tmp_path / "0002_second.sql").write_text("SELECT 2;")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;")
    (tmp_path / "0010_tenth.sql").write_text("SELECT 10;")
    (tmp_path / "README.md").write_text("ignored")
    migs = discover(tmp_path)
    assert [m.version for m in migs] == ["0001", "0002", "0010"]
    assert migs[0].sql == "SELECT 1;"


def test_discover_rejects_duplicate_prefix(tmp_path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 2;")
    with pytest.raises(ValueError, match="duplicate migration version"):
        discover(tmp_path)


def test_discover_missing_dir_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        discover(tmp_path / "nope")


def test_row_to_job_maps_columns() -> None:
    now = datetime(2026, 7, 25, tzinfo=UTC)
    row = (42, "emails", {"to": "a@b.c"}, 3, 1, 5, "dk", now)
    job = _row_to_job(row)
    assert job.id == 42
    assert job.queue == "emails"
    assert job.payload == {"to": "a@b.c"}
    assert job.priority == 3
    assert job.attempts == 1
    assert job.max_attempts == 5
    assert job.dedupe_key == "dk"
    assert job.attempts_remaining == 4
