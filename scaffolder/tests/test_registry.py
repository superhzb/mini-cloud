"""Tests for port allocation, router registration, and Grafana dashboard writing."""

from __future__ import annotations

import json
import urllib.error

import mini_scaffolder.registry as registry
from mini_scaffolder.registry import (
    allocate_ports,
    register_router,
    register_router_route,
    remote_route_entry,
    router_api_config,
    router_entry,
    write_grafana_dashboard,
)


def test_allocate_skips_used_and_reserved(tmp_path) -> None:
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps([{"name": "a", "port": 19201, "readinessPorts": [19202]}]))
    ports = allocate_ports(projects, need_web=True)
    assert ports.api == 19203  # 19201, 19202 taken
    assert ports.api != 19207  # reserved MLX port never handed out
    assert ports.web is not None and 19101 <= ports.web <= 19199


def test_allocate_no_file_starts_at_range_base(tmp_path) -> None:
    ports = allocate_ports(tmp_path / "nope.json", need_web=False)
    assert ports.api == 19201
    assert ports.web is None


def test_reserved_mlx_port_excluded(tmp_path) -> None:
    # Fill 19201..19206 so the next candidate would be 19207 (reserved) → must jump to 19208.
    used = [{"name": f"a{p}", "port": p} for p in range(19201, 19207)]
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps(used))
    ports = allocate_ports(projects, need_web=False)
    assert ports.api == 19208


def test_register_router_appends_and_dedupes(tmp_path) -> None:
    projects = tmp_path / "projects.json"
    projects.write_text("[]")
    entry = router_entry(
        "demo-x", "../demo-x", allocate_ports(projects, need_web=False), command="uv run demo-x"
    )
    assert register_router(projects, entry) is True
    # re-register same name → replaced, not duplicated
    assert register_router(projects, entry) is True
    data = json.loads(projects.read_text())
    assert [e["name"] for e in data] == ["demo-x"]
    assert data[0]["siteUrl"] == "https://demo-x.brettbot.ca"


def test_register_router_missing_repo_returns_false(tmp_path) -> None:
    missing = tmp_path / "no-such-dir" / "projects.json"
    assert register_router(missing, {"name": "x"}) is False


def test_write_grafana_dashboard(tmp_path) -> None:
    dash_dir = tmp_path / "dashboards"
    dash_dir.mkdir()
    out = write_grafana_dashboard(dash_dir, "demo-x")
    assert out is not None and out.is_file()
    dash = json.loads(out.read_text())
    assert dash["title"] == "app · demo-x"
    assert any("demo-x" in json.dumps(p) for p in dash["panels"])


def test_write_grafana_dashboard_missing_dir(tmp_path) -> None:
    assert write_grafana_dashboard(tmp_path / "nope", "x") is None


# --- route API config + registration ---------------------------------------------------------------


def test_router_api_config_none_without_token(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MINI_ROUTER_API_TOKEN", raising=False)
    (tmp_path / "brbot-router").mkdir()
    # .env with no ROUTE_REGISTRATION_TOKEN → API disabled.
    (tmp_path / "brbot-router" / ".env").write_text("DASHBOARD_DOMAIN=dash.test\nPORT=9100\n")
    assert router_api_config(tmp_path / "brbot-router" / "projects.json") is None


def test_router_api_config_reads_router_env(tmp_path, monkeypatch) -> None:
    for var in (
        "MINI_ROUTER_API_TOKEN",
        "MINI_ROUTER_API_URL",
        "MINI_ROUTER_API_HOST",
        "MINI_ROUTER_PORT",
    ):
        monkeypatch.delenv(var, raising=False)
    router = tmp_path / "brbot-router"
    router.mkdir()
    (router / ".env").write_text(
        "DASHBOARD_DOMAIN=dash.test\nPORT=9100\nROUTE_REGISTRATION_TOKEN=tok123\n"
    )
    api = router_api_config(router / "projects.json")
    assert api is not None
    assert api.token == "tok123"
    assert api.host == "dash.test"
    assert api.url == "http://127.0.0.1:9100"


def test_router_api_config_env_overrides_win(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_API_TOKEN", "envtok")
    monkeypatch.setenv("MINI_ROUTER_API_URL", "http://machine-a.local:9000")
    monkeypatch.setenv("MINI_ROUTER_API_HOST", "dashboard.brettbot.ca")
    api = router_api_config(tmp_path / "brbot-router" / "projects.json")
    assert api is not None
    assert (api.token, api.url, api.host) == (
        "envtok",
        "http://machine-a.local:9000",
        "dashboard.brettbot.ca",
    )


def test_register_route_falls_back_to_file_when_router_down(tmp_path, monkeypatch) -> None:
    projects = tmp_path / "projects.json"
    projects.write_text("[]")
    api = registry.RouterApi(url="http://127.0.0.1:9", token="t", host="dash.test")

    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(registry, "post_route", boom)
    result = register_router_route(projects, {"name": "demo", "port": 1}, api=api)
    assert result.via == "file" and result.ok is True
    assert [e["name"] for e in json.loads(projects.read_text())] == ["demo"]


def test_register_route_http_error_does_not_write_file(tmp_path, monkeypatch) -> None:
    projects = tmp_path / "projects.json"
    projects.write_text("[]")
    api = registry.RouterApi(url="http://127.0.0.1:9", token="bad", host="dash.test")

    def unauthorized(*_a, **_k):
        raise urllib.error.HTTPError("http://x/routes", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(registry, "post_route", unauthorized)
    result = register_router_route(projects, {"name": "demo", "port": 1}, api=api)
    assert result.via == "api" and result.ok is False
    # The live router rejected us → we must NOT silently write the file behind it.
    assert json.loads(projects.read_text()) == []


def test_register_route_api_success_skips_file(tmp_path, monkeypatch) -> None:
    projects = tmp_path / "projects.json"
    projects.write_text("[]")
    api = registry.RouterApi(url="http://127.0.0.1:9", token="t", host="dash.test")
    monkeypatch.setattr(registry, "post_route", lambda *_a, **_k: 201)
    result = register_router_route(projects, {"name": "demo", "port": 1}, api=api)
    assert result.via == "api" and result.ok is True
    assert json.loads(projects.read_text()) == []  # router owns the file, not us


def test_register_route_no_api_no_router_returns_none(tmp_path) -> None:
    missing = tmp_path / "no-such-dir" / "projects.json"
    result = register_router_route(missing, {"name": "x"}, api=None)
    assert result.via == "none" and result.ok is False


def test_remote_route_entry_shape() -> None:
    entry = remote_route_entry("demo-b", "demo-b.brettbot.ca", "machine-b.local", 19250)
    assert entry == {
        "name": "demo-b",
        "domain": "demo-b.brettbot.ca",
        "kind": "remote",
        "upstreamHost": "machine-b.local",
        "port": 19250,
        "siteUrl": "https://demo-b.brettbot.ca",
    }
