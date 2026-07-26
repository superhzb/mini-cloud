"""Integration test: `mini new` renders a real template and the result scores 7/7.

Uses the actual repo templates but redirects every side-effect (router file, infra dir, dest) into
tmp so the test never touches the real workspace. `setup` is off (no network), so we `touch`
uv.lock to simulate the post-`make setup` state the scorecard's bootstrap metric expects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_scaffolder.layout import Workspace
from mini_scaffolder.new import TEMPLATE_TYPES, _connect_host, run_new
from mini_scaffolder.score import score_repo

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "127.0.0.1"),  # unset → loopback
        ({"INFRA_BIND_ADDR": "127.0.0.1"}, "127.0.0.1"),
        ({"INFRA_BIND_ADDR": "0.0.0.0"}, "127.0.0.1"),  # bind wildcard is not connectable
        ({"INFRA_BIND_ADDR": ""}, "127.0.0.1"),
        ({"INFRA_BIND_ADDR": "192.168.0.12"}, "192.168.0.12"),  # LAN IP passes through
        ({"INFRA_BIND_ADDR": "0.0.0.0", "INFRA_CONNECT_ADDR": "192.168.0.12"}, "192.168.0.12"),
    ],
)
def test_connect_host_maps_bind_wildcard(env: dict[str, str], expected: str) -> None:
    assert _connect_host(env) == expected


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        mini_cloud=REPO_ROOT,
        templates=REPO_ROOT / "templates",
        infra=tmp_path / "infra",  # no scripts/grafana here → provision/dashboard no-op
        github_root=tmp_path / "gh",
    )


@pytest.mark.parametrize("app_type", TEMPLATE_TYPES)
def test_every_template_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_type: str
) -> None:
    monkeypatch.setenv("MINI_ROUTER_PROJECTS", str(tmp_path / "projects.json"))
    dest = tmp_path / f"demo-{app_type}"
    result = run_new(
        f"demo-{app_type}",
        app_type,
        workspace=_workspace(tmp_path),
        dest=dest,
        provision=False,
        git=False,
        setup=False,
    )
    assert result.dest == dest
    assert (dest / "README.md").is_file()
    assert (dest / "AGENTS.md").is_file()
    assert (dest / ".env").is_file()  # canonical env always written
    # router entry recorded
    projects = (tmp_path / "projects.json").read_text()
    assert f"demo-{app_type}" in projects


def test_fastapi_app_scores_7(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_PROJECTS", str(tmp_path / "projects.json"))
    dest = tmp_path / "demo-x"
    run_new(
        "demo-x",
        "fastapi",
        workspace=_workspace(tmp_path),
        dest=dest,
        provision=False,
        git=False,
        setup=False,
    )
    (dest / "uv.lock").write_text("# simulated post-`make setup` lockfile\n")

    card = score_repo(dest)
    failing = [c.metric for c in card.checks if not c.passed]
    assert card.score == 7, f"fresh fastapi app should be 7/7, failing: {failing}"


def test_no_web_port_for_fastapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_PROJECTS", str(tmp_path / "projects.json"))
    result = run_new(
        "demo-api",
        "fastapi",
        workspace=_workspace(tmp_path),
        dest=tmp_path / "demo-api",
        provision=False,
        git=False,
        setup=False,
    )
    assert result.web_port is None
    assert 19201 <= result.api_port <= 19299


def test_vite_gets_web_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINI_ROUTER_PROJECTS", str(tmp_path / "projects.json"))
    result = run_new(
        "demo-web",
        "vite",
        workspace=_workspace(tmp_path),
        dest=tmp_path / "demo-web",
        provision=False,
        git=False,
        setup=False,
    )
    assert result.web_port is not None
