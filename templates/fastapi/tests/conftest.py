"""Test fixtures. Live tests need real infra and are gated behind --run-live + canonical env, so
`make test`/`make check` stay green with no services running."""

from __future__ import annotations

import os

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
