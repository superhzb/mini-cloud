# mini-cloud

A reusable **local cloud** running on the Mac mini: a small set of shared services (Postgres,
object storage, observability, inference, and a common SDK) so that starting a new project is
nearly one command — database, storage, monitoring, ingress, and deployment already wired.

This is a **prototype factory**. Every project is an idea-demo. The platform optimizes for
*starting demos fast* and keeps a clean seam so *one* demo can graduate to a cheap VPS when it
matures. It is deliberately **not** a portable production platform, a multi-tenant SaaS, or a
Kubernetes story.

See [`docs/MINI_CLOUD_ARCHITECTURE.md`](docs/MINI_CLOUD_ARCHITECTURE.md) for the full design and
roadmap.

## Layout

```text
mini-cloud/
├── infra/        # Phase 1 — docker-compose infra stack (Postgres, MinIO, Loki, Prometheus, Grafana)
├── tooling/      # Phase 0 — shared base ruff/pyright/pytest config, referenced by every project
├── docs/         # architecture doc, ADRs, env-name + port registry, adoption guide
├── packages/     # Phase 2+ — SDK packages (config, db, storage, obs, inference), versioned independently
├── examples/     # in-repo reference apps (ref-fastapi) — SDK proof + 7/7 regression guard
├── scaffolder/   # Phase 4 — `mini new` + `mini score` CLI
└── templates/    # Phase 4 — app skeletons: fastapi (7/7) / vite / node
```

## Status

| Phase | What | State |
|---|---|---|
| 0 | Foundations & conventions (ADR, env/port registry, shared tooling) | ✅ done |
| 1 | Infra stack up (docker-compose) | ✅ done |
| 2 | SDK v0 (`config`, `db` + queue, `storage`, `obs`) + `ref-fastapi` | ✅ done |
| 3 | Inference SDK module (`inference`) | ✅ done |
| 4 | Scaffolder (`mini new`) + templates | ✅ done |
| 4.6 | Scorecard checker (`mini score`) + adoption guide | ✅ done |
| 4.5 | Remote route registration (touches brbot-router) | ✅ done |
| — | Analytics SDK (`mini-cloud-analytics`) + funnel/retention + Grafana | ✅ done |
| 5 | TS SDK + frontend template (`vite`/`node` templates still skeletons) | ▫️ not started |
| 6 | Identity — auth SDK (`mini-cloud-auth`) + ref-showcase proof + `identity` DB/infra | 🚧 partial (identity service + gateway trust external/pending) |
| 7 | Graduation playbook | ▫️ not started |

### Try it (no infra needed)

```bash
uv sync --all-packages                                   # install the whole workspace
uv run --package mini-cloud-db pytest packages/db        # SDK unit tests
uv run --package mini-scaffolder mini score examples/ref-fastapi   # → 7/7
```

## Quick start (infra)

Prerequisite: **Docker** (Docker Desktop or `colima`). Not currently installed on this machine —
`brew install --cask docker` or `brew install colima docker docker-compose`, then start the engine.

```bash
cd infra
cp .env.example .env      # loopback-only defaults are safe as-is
make up                   # docker compose up -d
make ps                   # health of every service
open http://localhost:13000   # Grafana (admin / see .env)
```

## Naming (fixed in Phase 0 — do not diverge)

One name family, per the architecture doc:

| Concept | Value |
|---|---|
| Repo / folder | `mini-cloud` |
| CLI verb | `mini` (`mini new`) |
| SDK scope | `@mini-cloud/…` |
| Canonical inference env | `MINI_INFERENCE_URL` |
| Router / infra entry | `mini-cloud-infra` |

See [`docs/env-and-ports.md`](docs/env-and-ports.md) for the full canonical env-var and port
registry.
