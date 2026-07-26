"""`mini route` — register/deregister a remote-upstream route on brbot-router (Phase 4.5).

This is the multi-machine *workflow (b)* piece: an app running on Machine B claims a
``*.brettbot.ca`` subdomain that Machine A's router proxies to it (no spawn, no idle-reap). Unlike
``mini new``'s local registration, there is **no projects.json file fallback** here — Machine A's
file is not on Machine B's disk — so the router must be reachable and the call fails loudly if not.

Requires the route API to be configured (``MINI_ROUTER_API_TOKEN`` / ``MINI_ROUTER_API_URL`` /
``MINI_ROUTER_API_HOST``, or the router's own ``.env`` when run beside it).
"""

from __future__ import annotations

import urllib.error
from dataclasses import dataclass

from .layout import Workspace, resolve_workspace
from .registry import RouterApi, delete_route, post_route, remote_route_entry, router_api_config


@dataclass(slots=True)
class RouteResult:
    action: str
    name: str
    domain: str | None
    status: int


def _require_api(workspace: Workspace | None) -> tuple[Workspace, RouterApi]:
    ws = workspace or resolve_workspace()
    api = router_api_config(ws.router_projects)
    if api is None:
        raise RuntimeError(
            "no route API configured — set MINI_ROUTER_API_TOKEN (and MINI_ROUTER_API_URL / "
            "MINI_ROUTER_API_HOST when targeting another machine's router)"
        )
    return ws, api


def _friendly(exc: Exception) -> RuntimeError:
    if isinstance(exc, urllib.error.HTTPError):
        return RuntimeError(f"router rejected the request ({exc.code} {exc.reason})")
    return RuntimeError(f"could not reach the router API: {exc}")


def run_route_add(
    name: str,
    domain: str,
    upstream_host: str,
    port: int,
    *,
    site_url: str | None = None,
    workspace: Workspace | None = None,
) -> RouteResult:
    _, api = _require_api(workspace)
    entry = remote_route_entry(name, domain, upstream_host, port, site_url=site_url)
    try:
        status = post_route(api, entry)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise _friendly(exc) from exc
    return RouteResult("add", name, domain, status)


def run_route_remove(name: str, *, workspace: Workspace | None = None) -> RouteResult:
    _, api = _require_api(workspace)
    try:
        status = delete_route(api, name)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise _friendly(exc) from exc
    return RouteResult("remove", name, None, status)
