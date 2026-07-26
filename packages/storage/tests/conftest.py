"""Gate live storage tests behind --run-live + STORAGE_* env."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that hit a real MinIO/S3"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") and os.environ.get("STORAGE_ENDPOINT"):
        return
    skip = pytest.mark.skip(reason="needs --run-live and STORAGE_* env")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
