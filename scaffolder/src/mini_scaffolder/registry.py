"""Port allocation, brbot-router registration, and Grafana dashboard provisioning.

These are the control-plane / observability side-effects of ``mini new``. Each is best-effort and
idempotent: a missing ``projects.json`` or Grafana dir is a warning, not a failure, so scaffolding
still produces a runnable app (the missing wiring is reported for the user to complete).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Application port ranges from docs/env-and-ports.md. 19207 is reserved for the MLX gateway.
WEB_RANGE = range(19101, 19200)
API_RANGE = range(19201, 19300)
RESERVED_PORTS = frozenset({19207})


@dataclass(frozen=True, slots=True)
class Ports:
    api: int
    web: int | None


def _used_ports(router_projects: Path) -> set[int]:
    """Collect ports already claimed in brbot-router's projects.json (any int under known keys)."""
    if not router_projects.is_file():
        return set()
    used: set[int] = set()
    try:
        data = json.loads(router_projects.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return used
    entries = data if isinstance(data, list) else data.get("projects", [])
    for entry in entries if isinstance(entries, list) else []:
        for key in ("port", "apiPort", "webPort"):
            val = entry.get(key) if isinstance(entry, dict) else None
            if isinstance(val, int):
                used.add(val)
        for key in ("readinessPorts", "ports"):
            vals = entry.get(key) if isinstance(entry, dict) else None
            if isinstance(vals, list):
                used.update(v for v in vals if isinstance(v, int))
    return used


def allocate_ports(router_projects: Path, *, need_web: bool) -> Ports:
    """Pick the next free API port (and web port if needed) from the registry ranges, skipping
    ports already in projects.json and the reserved MLX port."""
    used = _used_ports(router_projects) | RESERVED_PORTS
    api = next((p for p in API_RANGE if p not in used), None)
    if api is None:
        raise RuntimeError("no free API port in 19201–19299")
    used.add(api)
    web: int | None = None
    if need_web:
        web = next((p for p in WEB_RANGE if p not in used), None)
        if web is None:
            raise RuntimeError("no free web port in 19101–19199")
    return Ports(api=api, web=web)


def router_entry(name: str, path: str, ports: Ports, *, command: str) -> dict[str, object]:
    """Build the lazy-spawn projects.json entry for a scaffolded app."""
    readiness = [ports.api] + ([ports.web] if ports.web else [])
    return {
        "name": name,
        "path": path,
        "command": command,
        "port": ports.api,
        "readinessPorts": readiness,
        "siteUrl": f"https://{name}.brettbot.ca",
    }


def register_router(router_projects: Path, entry: dict[str, object]) -> bool:
    """Append ``entry`` to projects.json (creating a list file if absent). Idempotent on ``name``.
    Returns True if written, False if the file was missing and we did not create the router repo."""
    if not router_projects.parent.is_dir():
        return False  # brbot-router repo not present here — caller warns
    data: object
    if router_projects.is_file():
        data = json.loads(router_projects.read_text("utf-8"))
    else:
        data = []
    entries = data if isinstance(data, list) else data.get("projects", [])  # type: ignore[union-attr]
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if not (isinstance(e, dict) and e.get("name") == entry["name"])]
    entries.append(entry)
    out: object = entries if isinstance(data, list) else {**data, "projects": entries}  # type: ignore[dict-item]
    router_projects.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return True


# --- Live route registration (POST /routes) -------------------------------------------------------
#
# Approach A (see docs/MINI_CLOUD_ARCHITECTURE.md, Phase 4.5): the router owns projects.json, so
# when it is *running* we register through its authenticated `POST /routes` API rather than editing
# the file out-of-band (which would race the router's in-memory state). When the router is *down*
# there is no live state to race, so a direct file write is safe and keeps `mini new` working.


@dataclass(frozen=True, slots=True)
class RouterApi:
    """Where and how to reach the router's route-registration API."""

    url: str  # base, e.g. http://127.0.0.1:9000
    token: str  # ROUTE_REGISTRATION_TOKEN
    host: str  # Host header — the API lives on the dashboard host


@dataclass(frozen=True, slots=True)
class RouteRegistration:
    via: str  # "api" | "file" | "none"
    ok: bool
    detail: str


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"")
    return env


def router_api_config(router_projects: Path) -> RouterApi | None:
    """Resolve the route API from env overrides + the router's own ``.env``.

    Returns ``None`` (→ callers fall back to the file) when no registration token is configured.
    Env overrides (``MINI_ROUTER_API_URL`` / ``_TOKEN`` / ``_HOST`` / ``MINI_ROUTER_PORT``) let a
    Machine-B scaffolder target Machine A's router.
    """
    env = _read_env_file(router_projects.parent / ".env")
    token = os.environ.get("MINI_ROUTER_API_TOKEN") or env.get("ROUTE_REGISTRATION_TOKEN")
    if not token:
        return None
    port = os.environ.get("MINI_ROUTER_PORT") or env.get("PORT") or "9000"
    url = os.environ.get("MINI_ROUTER_API_URL") or f"http://127.0.0.1:{port}"
    host = os.environ.get("MINI_ROUTER_API_HOST") or env.get("DASHBOARD_DOMAIN") or "localhost"
    return RouterApi(url=url, token=token, host=host)


def post_route(api: RouterApi, entry: dict[str, object], *, timeout: float = 5.0) -> int:
    """POST ``entry`` to ``{api.url}/routes``. Returns the HTTP status; raises ``urllib`` errors on
    connection failure and ``HTTPError`` on a non-2xx response."""
    data = json.dumps(entry).encode("utf-8")
    request = urllib.request.Request(
        f"{api.url.rstrip('/')}/routes",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api.token}",
            # The API is scoped to the dashboard host so it never shadows an app's own /routes path.
            "Host": api.host,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 (trusted local URL)
        return int(resp.status)


def delete_route(api: RouterApi, name: str, *, timeout: float = 5.0) -> int:
    request = urllib.request.Request(
        f"{api.url.rstrip('/')}/routes/{name}",
        method="DELETE",
        headers={"Authorization": f"Bearer {api.token}", "Host": api.host},
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        return int(resp.status)


def register_router_route(
    router_projects: Path, entry: dict[str, object], *, api: RouterApi | None
) -> RouteRegistration:
    """Register ``entry`` via the live router API when reachable, else write projects.json directly.

    A **connection** failure (router down) falls back to the file — safe, since there is no live
    state to race. An **HTTP error** (router up but rejected us: bad token/entry) is surfaced and
    does *not* write the file behind a live router, which would silently diverge until a restart.
    """
    if api is not None:
        try:
            status = post_route(api, entry)
            return RouteRegistration("api", True, f"registered via POST /routes ({status})")
        except urllib.error.HTTPError as exc:
            return RouteRegistration(
                "api",
                False,
                f"router rejected POST /routes ({exc.code} {exc.reason}); route NOT written — "
                "check ROUTE_REGISTRATION_TOKEN / entry (no file write behind a live router)",
            )
        except (urllib.error.URLError, OSError, TimeoutError):
            pass  # router unreachable — safe to write the file; it loads on next router start
    written = register_router(router_projects, entry)
    if written:
        where = "router not running — wrote projects.json (loads on next start)"
        return RouteRegistration("file", True, where)
    return RouteRegistration("none", False, "brbot-router projects.json not found")


def remote_route_entry(
    name: str, domain: str, upstream_host: str, port: int, *, site_url: str | None = None
) -> dict[str, object]:
    """Build a ``kind:"remote"`` route entry (Phase 4.5, multi-machine workflow *b*)."""
    entry: dict[str, object] = {
        "name": name,
        "domain": domain,
        "kind": "remote",
        "upstreamHost": upstream_host,
        "port": port,
    }
    entry["siteUrl"] = site_url if site_url else f"https://{domain}"
    return entry


def write_grafana_dashboard(dashboards_dir: Path, name: str) -> Path | None:
    """Drop a minimal per-app dashboard into the provisioned Grafana dashboards dir. Returns the
    path, or None if the dir doesn't exist (infra not present)."""
    if not dashboards_dir.is_dir():
        return None
    dash = _dashboard_json(name)
    target = dashboards_dir / f"app-{name}.json"
    target.write_text(json.dumps(dash, indent=2) + "\n", encoding="utf-8")
    return target


def _dashboard_json(name: str) -> dict[str, object]:
    """A tiny dashboard: request rate + p95 latency from the app's Prometheus metrics, and its
    logs from Loki — filtered to this app's label."""
    return {
        "title": f"app · {name}",
        "uid": f"app-{name}"[:40],
        "tags": ["mini-cloud", "app", name],
        "timezone": "browser",
        "schemaVersion": 39,
        "panels": [
            {
                "type": "timeseries",
                "title": "request rate (req/s)",
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "targets": [
                    {
                        "expr": f'sum(rate(http_requests_total{{app="{name}"}}[5m]))',
                        "datasource": {"type": "prometheus"},
                    }
                ],
            },
            {
                "type": "timeseries",
                "title": "p95 latency (s)",
                "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                "targets": [
                    {
                        "expr": (
                            "histogram_quantile(0.95, sum(rate("
                            f'http_request_duration_seconds_bucket{{app="{name}"}}[5m])) by (le))'
                        ),
                        "datasource": {"type": "prometheus"},
                    }
                ],
            },
            {
                "type": "logs",
                "title": "logs",
                "gridPos": {"h": 10, "w": 24, "x": 0, "y": 8},
                "targets": [{"expr": f'{{app="{name}"}}', "datasource": {"type": "loki"}}],
            },
        ],
    }
