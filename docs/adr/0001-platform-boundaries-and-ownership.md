# ADR 0001: mini-cloud platform boundaries and ownership

- Status: Accepted
- Date: 2026-07-24
- Related: `mlx-platform` ADR 0001 (inference ownership), which this platform incorporates
  unchanged.

## Context

The workspace on this machine hosts `brbot-router` (ingress + process orchestration),
`mlx-platform` (inference authority), and ~11 sibling demo projects. Reviewing them surfaced the
same infrastructure re-invented many times over:

- **State/queues (4×):** a SQLite + WAL + single-writer + job-queue stack in `fr-hub-api`
  (`SqliteWriter`), `tk-orchestrator` (SQLAlchemy), `srt-flow/pkg-job-orch`, and `mlx-platform`
  records.
- **Object storage (5+×):** the filesystem used as an object store — `hub-api/.data`, `srt-api`
  JSON files, `srt-flow` `STORAGE_ROOT`, `mlx-audio`, `~/Public/*`.
- **Inference clients (N×):** hand-rolled clients in `fr-hub-web`, `tk-web`, `hub-gateway`,
  `pkg-llm-backend`, pointed at **three different** gateway URLs (`127.0.0.1:8933`,
  `192.168.0.12:9000`, `127.0.0.1:5900`).
- **Tooling config (~25×):** ruff + pyright config copied verbatim into every `pyproject.toml`.
- **Logging:** per-app logging env and JSONL directories, with no shared pane of glass.

None of this is product logic; it is undifferentiated infrastructure that every demo re-pays for.

## Decision

Add a thin **mini-cloud** platform layer on top of the two things that already work
(`brbot-router`, `mlx-platform`) providing shared, contract-based infrastructure:

1. A **docker-compose infra stack** (Postgres, MinIO, Loki, Prometheus, Grafana) — the *same* file
   runs unchanged on a Linux VPS.
2. A **thin, multi-language SDK** (`config`, `db`, `storage`, `inference`, `obs`) with each package
   versioned independently and pinned by consumers.
3. A **one-command scaffolder** (`mini new`) that provisions DB + bucket, registers the app with
   `brbot-router` + a subdomain, writes canonical config, and wires the SDK.

The platform owns **shared infrastructure and conventions only**. It does **not** own product
logic, prompts, business workflows, or any app's data model.

## Ownership rules

| Concern | Owner |
|---|---|
| Relational state, migrations, job queue (the *primitive*) | **Platform** (Postgres + `db` SDK) |
| A project's schema, tables, and query logic | Application |
| Object storage service + per-project bucket | **Platform** (MinIO + `storage` SDK) |
| What objects mean and how they're produced | Application |
| Inference execution + the shared Apple GPU | **`mlx-platform`** (unchanged; reached over HTTP) |
| Prompts, batching, output parsing, retry semantics | Application |
| Ingress, host routing, lazy spawn, idle reap, dashboard | **`brbot-router`** (unchanged) |
| Which apps exist and what commands run them | Application (registered via scaffolder) |
| Logs, metrics, dashboards (the *pipes* + one Grafana) | **Platform** (`obs` SDK + infra stack) |
| What to log and which metrics matter | Application |
| Canonical env-var names + port registry | **Platform** (`docs/env-and-ports.md`) |
| Identity / auth (JWT issuance + verify) | **Deferred** (future `identity` service, Phase 6) |

## Boundaries (contracts, not imports)

Every seam between an app and the platform is a **wire contract**, never a shared code import
across a service boundary:

- Database: **Postgres wire protocol** (`DATABASE_URL`).
- Object storage: **S3 API** (`STORAGE_ENDPOINT` + access keys).
- Inference: **OpenAI-compatible HTTP** (`MINI_INFERENCE_URL`).
- Logs/metrics: **Loki push + Prometheus/OTLP scrape**.
- Ingress: `brbot-router` `projects.json` registration + `/healthz`, `/readyz`.

Because apps only ever speak these contracts over config, an app graduates to a VPS by repointing
env and DNS — no code rewrite. Any implementation (self-hosted or managed) can be swapped
underneath a contract.

## Consequences

- **Runtime independence:** each service (`brbot-router`, `mlx-platform`, future `identity`)
  deploys on its own and talks to others only over contracts.
- **Version independence:** SDK packages are versioned independently and pinned; App A can stay on
  `db@0.4` while App B moves to `db@0.5`.
- The infra stack is a **new always-on dependency**. It registers as one `alwaysOn` entry in
  `brbot-router/projects.json` (`mini-cloud-infra`) and requires a container engine (Docker/colima)
  on the host — a new prerequisite this machine must install.
- MLX inference stays **native and Mac-only** (Apple-GPU cannot run in Linux containers); apps reach
  it over HTTP and flip one env var to a cloud provider on graduation.

## Non-goals (restated)

Not Kubernetes, not multi-node, not HA. Not a multi-tenant SaaS or shared business-logic library.
Not a container runtime for the apps themselves (apps stay native processes under `brbot-router`).
Not a secrets vault (a lightweight `.env.example` convention is in scope). Not a rebuild of
`brbot-router` or `mlx-platform`.
