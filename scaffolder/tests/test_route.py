"""Tests for `mini route` (remote-upstream registration, Phase 4.5)."""

from __future__ import annotations

import urllib.error

import pytest

import mini_scaffolder.route as route
from mini_scaffolder.layout import Workspace


def _workspace(tmp_path) -> Workspace:
    return Workspace(mini_cloud=tmp_path, templates=tmp_path, infra=tmp_path, github_root=tmp_path)


def test_route_add_requires_api(tmp_path, monkeypatch) -> None:
    for var in ("MINI_ROUTER_API_TOKEN", "MINI_ROUTER_API_URL", "MINI_ROUTER_API_HOST"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="no route API configured"):
        route.run_route_add(
            "demo-b", "demo-b.test", "machine-b.local", 19250, workspace=_workspace(tmp_path)
        )


def test_route_add_posts_remote_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_API_TOKEN", "tok")
    monkeypatch.setenv("MINI_ROUTER_API_URL", "http://machine-a.local:9000")
    monkeypatch.setenv("MINI_ROUTER_API_HOST", "dash.test")

    captured: dict[str, object] = {}

    def fake_post(api, entry, **_k):
        captured["api"] = api
        captured["entry"] = entry
        return 201

    monkeypatch.setattr(route, "post_route", fake_post)

    result = route.run_route_add(
        "demo-b",
        "demo-b.brettbot.ca",
        "machine-b.local",
        19250,
        workspace=_workspace(tmp_path),
    )
    assert result.status == 201
    assert captured["entry"] == {
        "name": "demo-b",
        "domain": "demo-b.brettbot.ca",
        "kind": "remote",
        "upstreamHost": "machine-b.local",
        "port": 19250,
        "siteUrl": "https://demo-b.brettbot.ca",
    }
    assert captured["api"].host == "dash.test"


def test_route_add_surfaces_http_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_API_TOKEN", "tok")

    def fake_post(*_a, **_k):
        raise urllib.error.HTTPError("http://x/routes", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(route, "post_route", fake_post)
    with pytest.raises(RuntimeError, match="401"):
        route.run_route_add(
            "demo-b", "demo-b.test", "machine-b.local", 19250, workspace=_workspace(tmp_path)
        )


def test_route_remove_calls_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_API_TOKEN", "tok")
    seen: dict[str, object] = {}

    def fake_delete(api, name, **_k):
        seen["name"] = name
        return 200

    monkeypatch.setattr(route, "delete_route", fake_delete)
    result = route.run_route_remove("demo-b", workspace=_workspace(tmp_path))
    assert result.status == 200
    assert seen["name"] == "demo-b"
