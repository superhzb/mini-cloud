"""Offline routing and packaging checks for the zero-build web console."""

from __future__ import annotations

import os
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ref_showcase.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    for variable in (
        "DATABASE_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "MINI_INFERENCE_URL",
        "MINI_ANALYTICS_DSN",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app()) as test_client:
        yield test_client


def test_console_redirect_discovery_and_assets(client: TestClient) -> None:
    redirect = client.get("/ui", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/ui/"

    index = client.get("/ui/")
    css = client.get("/ui/console.css")
    javascript = client.get("/ui/console.js")
    assert index.status_code == css.status_code == javascript.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert css.headers["content-type"].startswith("text/css")
    assert javascript.headers["content-type"].startswith(
        ("text/javascript", "application/javascript")
    )
    assert client.get("/").json()["ui"] == "/ui/"


def test_index_references_only_resolving_local_assets(client: TestClient) -> None:
    index = client.get("/ui/").text
    assert 'href="console.css"' in index
    assert 'src="console.js"' in index
    assert "https://" not in index
    assert "http://" not in index
    assert client.get("/ui/console.css").status_code == 200
    assert client.get("/ui/console.js").status_code == 200


def test_built_wheel_contains_console_assets(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=project,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("ref_showcase-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert {
        "ref_showcase/web/index.html",
        "ref_showcase/web/console.css",
        "ref_showcase/web/console.js",
    } <= names
