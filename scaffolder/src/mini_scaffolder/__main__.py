"""CLI entrypoint for `mini`: `mini new` (scaffold) and `mini score` (readiness check)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .new import TEMPLATE_TYPES, run_new
from .route import run_route_add, run_route_remove
from .score import format_scorecard, score_repo


def _cmd_new(args: argparse.Namespace) -> int:
    try:
        result = run_new(
            args.name,
            args.type,
            dest=Path(args.path) if args.path else None,
            provision=not args.no_provision,
            git=not args.no_git,
            setup=not args.no_setup,
        )
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f"mini new: {exc}", file=sys.stderr)
        return 1

    web = f" · web :{result.web_port}" if result.web_port else ""
    print(f"✓ scaffolded '{result.name}' ({result.app_type}) at {result.dest}")
    print(f"  ports: api :{result.api_port}{web}")
    print(f"  provisioned DB+bucket: {'yes' if result.provisioned else 'no'}")
    route_state = "registered" if result.router_registered else "not registered"
    print(f"  brbot-router route:    {route_state}")
    print(f"  grafana dashboard:     {'yes' if result.grafana_dashboard else 'no'}")
    print(f"  deps installed:        {'yes' if result.setup_ran else 'no'}")
    print(f"  git initialized:       {'yes' if result.git_initialized else 'no'}")
    for note in result.notes:
        print(f"  • {note}")
    print(f"\nnext:  cd {result.dest}  &&  make setup  &&  make check")
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    try:
        if args.route_command == "add":
            result = run_route_add(
                args.name,
                args.domain,
                args.upstream_host,
                args.port,
                site_url=args.site_url,
            )
            print(f"✓ registered remote route '{result.name}' → {args.upstream_host}:{args.port}")
            print(f"  {result.domain} proxied by brbot-router (HTTP {result.status})")
            print("  note: create the Cloudflare DNS record for this subdomain separately")
        else:
            result = run_route_remove(args.name)
            print(f"✓ removed route '{result.name}' (HTTP {result.status})")
    except (ValueError, RuntimeError) as exc:
        print(f"mini route: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    card = score_repo(Path(args.repo))
    print(format_scorecard(card))
    if args.min is not None and card.score < args.min:
        print(
            f"\nscore {card.score}/{card.total} is below required minimum {args.min}",
            file=sys.stderr,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini", description="mini-cloud app scaffolder + scorer")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="scaffold + provision a new app")
    new.add_argument("name", help="app name, [a-z][a-z0-9-]* (e.g. demo-x)")
    new.add_argument(
        "--type", choices=TEMPLATE_TYPES, default="fastapi", help="app template (default: fastapi)"
    )
    new.add_argument("--path", help="destination dir (default: ../<name> sibling)")
    new.add_argument("--no-provision", action="store_true", help="don't create DB + bucket")
    new.add_argument("--no-setup", action="store_true", help="don't install deps / make a lockfile")
    new.add_argument("--no-git", action="store_true", help="don't run git init")
    new.set_defaults(func=_cmd_new)

    route = sub.add_parser(
        "route", help="register/deregister a remote-upstream route (multi-machine workflow b)"
    )
    route_sub = route.add_subparsers(dest="route_command", required=True)
    route_add = route_sub.add_parser("add", help="proxy a subdomain to an app on another machine")
    route_add.add_argument("name", help="route name (unique in projects.json)")
    route_add.add_argument("--domain", required=True, help="public host, e.g. app.brettbot.ca")
    route_add.add_argument(
        "--upstream-host", required=True, help="host running the app, e.g. machine-b.local"
    )
    route_add.add_argument("--port", required=True, type=int, help="port the app listens on")
    route_add.add_argument("--site-url", help="dashboard deep-link (default https://<domain>)")
    route_add.set_defaults(func=_cmd_route)
    route_remove = route_sub.add_parser("remove", help="deregister a route by name")
    route_remove.add_argument("name", help="route name to remove")
    route_remove.set_defaults(func=_cmd_route)

    score = sub.add_parser("score", help="score a repo 0–7 against the scorecard")
    score.add_argument("repo", nargs="?", default=".", help="repo dir (default: .)")
    score.add_argument("--min", type=int, help="exit non-zero if score is below this")
    score.set_defaults(func=_cmd_score)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
