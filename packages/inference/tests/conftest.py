"""Gate live inference tests behind --run-live + MINI_INFERENCE_URL."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that hit a real gateway"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live") and os.environ.get("MINI_INFERENCE_URL"):
        return
    skip = pytest.mark.skip(reason="needs --run-live and MINI_INFERENCE_URL")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
