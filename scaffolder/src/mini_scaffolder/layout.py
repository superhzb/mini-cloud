"""Locate the workspace directories the scaffolder reads and writes.

The scaffolder runs from inside the ``mini-cloud`` repo (or with env overrides), and touches a few
well-known siblings: the ``templates/`` dir, the ``infra/`` stack (for provisioning + Grafana), and
``brbot-router/projects.json`` (for route registration). Everything is overridable by env so the
tool works from Machine B (remote workflow *a*) and in tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Workspace:
    """Resolved paths. ``github_root`` is the flat sibling dir holding mini-cloud, brbot-router …"""

    mini_cloud: Path
    templates: Path
    infra: Path
    github_root: Path

    @property
    def router_projects(self) -> Path:
        """``brbot-router/projects.json`` — may not exist; registration is best-effort."""
        override = os.environ.get("MINI_ROUTER_PROJECTS")
        if override:
            return Path(override)
        return self.github_root / "brbot-router" / "projects.json"

    @property
    def grafana_dashboards(self) -> Path:
        return self.infra / "config" / "grafana" / "dashboards"


def find_mini_cloud_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (or this file) to the ``mini-cloud`` repo root — the dir containing
    both ``templates/`` and ``infra/``. Falls back to the env override ``MINI_CLOUD_ROOT``."""
    override = os.environ.get("MINI_CLOUD_ROOT")
    if override:
        return Path(override)
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "templates").is_dir() and (parent / "infra").is_dir():
            return parent
    raise FileNotFoundError(
        "could not locate the mini-cloud repo root (needs templates/ + infra/); set MINI_CLOUD_ROOT"
    )


def resolve_workspace(start: Path | None = None) -> Workspace:
    root = find_mini_cloud_root(start)
    templates = Path(os.environ.get("MINI_TEMPLATES_DIR", root / "templates"))
    return Workspace(
        mini_cloud=root,
        templates=templates,
        infra=root / "infra",
        github_root=root.parent,
    )
