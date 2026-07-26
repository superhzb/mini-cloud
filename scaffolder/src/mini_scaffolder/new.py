"""`mini new` — provision + scaffold a new app in one command.

Orchestrates: allocate ports → render the template → provision DB + bucket → write canonical
``.env`` → register the brbot-router route → drop a Grafana dashboard → ``git init``. Control-plane
and observability side-effects are best-effort and reported; the generated app is always runnable.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .layout import Workspace, resolve_workspace
from .provision import provision_db_and_bucket
from .registry import (
    allocate_ports,
    register_router_route,
    router_api_config,
    router_entry,
    write_grafana_dashboard,
)
from .render import render_tree

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}$")

# Per-template wiring: whether it needs a web port, and how brbot-router spawns it.
TEMPLATE_META: dict[str, dict[str, object]] = {
    "fastapi": {"need_web": False, "command": "uv run {name}"},
    "vite": {"need_web": True, "command": "npm run dev"},
    "node": {"need_web": False, "command": "npm start"},
}
TEMPLATE_TYPES = tuple(TEMPLATE_META)

# Canonical service ports (docs/env-and-ports.md). The host for these is the infra bind addr, not a
# hardcoded loopback — so an app scaffolded against a LAN-bound stack (INFRA_BIND_ADDR = a host IP)
# reaches Loki and the inference gateway on the infra HOST, not its own loopback. On the default
# loopback bind both resolve to 127.0.0.1 exactly as before. (The MLX gateway is native, not in the
# compose stack, but it runs on the same host, so the bind addr is the right host for it too.)
INFERENCE_PORT = 19207
LOKI_PORT = 13100


# How each template installs deps + produces a lockfile (scorecard #1). Best-effort.
SETUP_COMMANDS: dict[str, list[str]] = {
    "fastapi": ["uv", "sync"],
    "vite": ["npm", "install"],
    "node": ["npm", "install"],
}


@dataclass(slots=True)
class NewResult:
    name: str
    app_type: str
    dest: Path
    api_port: int
    web_port: int | None
    provisioned: bool
    router_registered: bool
    grafana_dashboard: Path | None
    git_initialized: bool
    setup_ran: bool = False
    notes: list[str] = field(default_factory=list)


def validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid name {name!r}: use [a-z][a-z0-9-]* (2–39 chars), e.g. 'demo-x'")


def _infra_env(infra_dir: Path) -> dict[str, str]:
    """Read the infra stack's .env (STORAGE keys, bind addr) so the app's .env points at it."""
    env: dict[str, str] = {}
    f = infra_dir / ".env"
    if f.is_file():
        for line in f.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _env_file(
    name: str, api_port: int, db_password: str, infra_env: dict[str, str], *, provisioned: bool
) -> str:
    bind = infra_env.get("INFRA_BIND_ADDR", "127.0.0.1")
    access = infra_env.get("STORAGE_ACCESS_KEY", "minioadmin")
    secret = infra_env.get("STORAGE_SECRET_KEY", "minioadmin_dev_change_me")
    # No inline comments inside a value — the dotenv parser treats the rest of the line as the
    # value. Any hint goes on its own comment line above.
    db_pw = db_password if provisioned else "CHANGE_ME"
    db_comment = (
        "# database (mini_cloud.db)"
        if provisioned
        else f"# database — provision first: make -C ../mini-cloud/infra project NAME={name}"
    )
    return "\n".join(
        [
            f"# {name} — canonical env from `mini new`. Never commit (.gitignore has .env).",
            f"APP_NAME={name}",
            "APP_ENV=dev",
            f"PORT={api_port}",
            "LOG_LEVEL=info",
            "",
            db_comment,
            f"DATABASE_URL=postgresql://{name}:{db_pw}@{bind}:15432/{name}",
            "",
            "# object storage (mini_cloud.storage)",
            f"STORAGE_ENDPOINT=http://{bind}:19000",
            f"STORAGE_ACCESS_KEY={access}",
            f"STORAGE_SECRET_KEY={secret}",
            f"STORAGE_BUCKET={name}",
            "STORAGE_REGION=us-east-1",
            "",
            "# observability (mini_cloud.obs)",
            f"LOKI_URL=http://{bind}:{LOKI_PORT}",
            "",
            "# inference (mini_cloud.inference)",
            f"MINI_INFERENCE_URL=http://{bind}:{INFERENCE_PORT}/v1",
            "",
        ]
    )


def run_new(
    name: str,
    app_type: str,
    *,
    workspace: Workspace | None = None,
    dest: Path | None = None,
    provision: bool = True,
    git: bool = True,
    setup: bool = True,
) -> NewResult:
    validate_name(name)
    if app_type not in TEMPLATE_META:
        raise ValueError(f"unknown --type {app_type!r}; choose from {', '.join(TEMPLATE_TYPES)}")

    ws = workspace or resolve_workspace()
    package = name.replace("-", "_")
    meta = TEMPLATE_META[app_type]
    need_web = bool(meta["need_web"])
    dest = Path(dest) if dest else ws.github_root / name

    ports = allocate_ports(ws.router_projects, need_web=need_web)
    notes: list[str] = []

    variables = {
        "name": name,
        "package": package,
        "description": f"A mini-cloud {app_type} app.",
        "api_port": str(ports.api),
        "web_port": str(ports.web or ""),
        "sdk_version": ">=0.1.0",
    }
    render_tree(ws.templates / app_type, dest, variables)

    # provision DB + bucket
    db_password = "CHANGE_ME"
    provisioned = False
    if provision:
        result = provision_db_and_bucket(ws.infra, name)
        provisioned = result.ok
        db_password = result.db_password
        notes.append(result.detail if result.ok else f"provisioning skipped: {result.detail}")
    else:
        notes.append("provisioning skipped (--no-provision)")

    # canonical .env (only for python-flavoured apps that read it; harmless otherwise)
    (dest / ".env").write_text(
        _env_file(name, ports.api, db_password, _infra_env(ws.infra), provisioned=provisioned),
        encoding="utf-8",
    )

    # register the router route (best-effort). Prefer the live POST /routes API (approach A); fall
    # back to a direct projects.json write only when the router is down (no live state to race).
    rel_path = f"../{dest.name}"
    entry = router_entry(name, rel_path, ports, command=str(meta["command"]).format(name=name))
    registration = register_router_route(
        ws.router_projects, entry, api=router_api_config(ws.router_projects)
    )
    router_registered = registration.ok
    notes.append(f"router route: {registration.detail}")
    if not router_registered and registration.via == "none":
        notes.append(f"add this entry to {ws.router_projects} manually:\n  {entry}")

    # grafana dashboard (best-effort)
    dashboard = write_grafana_dashboard(ws.grafana_dashboards, name)
    if dashboard is None:
        notes.append("infra Grafana dir not found; dashboard not provisioned")

    # install deps + produce a lockfile so the app is bootstrap-self-sufficient (scorecard #1)
    setup_ran = False
    if setup:
        setup_ran = _run_setup(dest, app_type)
        notes.append(
            "deps installed (lockfile written)"
            if setup_ran
            else "setup skipped — run `make setup` (needed for a lockfile / scorecard #1)"
        )

    # git init (best-effort) — after setup so the lockfile is part of the initial tree
    git_ok = False
    if git:
        git_ok = _git_init(dest)
        if not git_ok:
            notes.append("git init skipped (git unavailable or already a repo)")

    return NewResult(
        name=name,
        app_type=app_type,
        dest=dest,
        api_port=ports.api,
        web_port=ports.web,
        provisioned=provisioned,
        router_registered=router_registered,
        grafana_dashboard=dashboard,
        git_initialized=git_ok,
        setup_ran=setup_ran,
        notes=notes,
    )


def _run_setup(dest: Path, app_type: str) -> bool:
    cmd = SETUP_COMMANDS.get(app_type)
    if not cmd:
        return False
    try:
        subprocess.run(cmd, cwd=str(dest), check=True, capture_output=True, timeout=600)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _git_init(dest: Path) -> bool:
    if (dest / ".git").exists():
        return False
    try:
        subprocess.run(
            ["git", "init", "-q"], cwd=str(dest), check=True, capture_output=True, timeout=30
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False
