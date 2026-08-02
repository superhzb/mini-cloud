# mini-cloud — Architecture & Roadmap

> **Scope.** This describes a reusable "local cloud" running on the Mac mini: a small set of
> shared services (Postgres, object storage, observability, inference, and a common SDK) so that
> starting a new project is nearly one command, with database, storage, monitoring, ingress, and
> deployment already wired.
>
> **Purpose.** This is a **prototype factory**. Every project is an idea-demo. The platform
> optimizes for *starting demos fast* and keeps a clean seam so that *one* demo can graduate to a
> cheap VPS when it matures. It is deliberately **not** a portable production platform, a
> multi-tenant SaaS, or a Kubernetes story.
>
> **Companion docs.** Inference already has its own home: `MLX_PLATFORM_ARCHITECTURE_REVIEW.md`
> and `MLX_PLATFORM_CONSUMER_MIGRATION.md`. This platform *incorporates* `mlx-platform` as its
> inference service and does not redesign it.
>
> **Boundary (this plan's core rule).** This plan builds **only the `mini-cloud` project itself** —
> infra stack, SDK, scaffolder, templates, and a documented *adoption standard*. It **does not
> modify any downstream project**. Existing apps are never migrated *by* this plan; they adopt the
> standard later, on their own schedule, by following the adoption workflow. New apps get the
> standard for free through `mini new`. Every reference to another repo below is **motivation or a
> future opt-in target**, never work this plan performs on that repo.

## Decision

Build a thin local platform layer on top of two things that already work — `brbot-router`
(ingress + process orchestration) and `mlx-platform` (inference authority) — by adding:

1. A **docker-compose infra stack** for stateful shared services (Postgres, MinIO, Loki,
   Prometheus, Grafana). The same compose file is the cloud-migration story: it runs unchanged on
   a Linux VPS.
2. A **thin SDK** (Python + TypeScript) that offers a common set of config, database, storage,
   inference, and observability primitives, so a project that adopts it *can* retire its bespoke
   SQLite/job-queue stack and hand-rolled inference client — an opt-in convergence, not a
   migration this plan carries out.
3. A **one-command scaffolder** (`mini new`) that provisions a database and bucket, registers
   the app with `brbot-router` and a subdomain, writes canonical config, and wires the SDK.

The platform owns shared infrastructure and conventions. It does **not** own product logic,
prompts, business workflows, or any app's data model — and it does **not** reach into any existing
repo. It publishes a standard (SDK + templates + scorecard + adoption workflow); repos come to the
standard when their owner decides to.

## Design principles

1. **Prototypes first, graduation-ready.** Optimize for the speed of starting a demo. Keep one
   clean seam (config + SDK) so a matured app can leave for a VPS without rewrites.
2. **Reuse what works.** `brbot-router` stays the ingress/orchestrator; `mlx-platform` stays the
   inference authority. Neither is rebuilt.
3. **Boring, portable infra.** The infra stack is standard software behind standard protocols
   (Postgres wire, S3, OTLP/Loki, PromQL). A cheap VPS is a single Linux box, so "migrate to
   cloud" means *run the same compose file there* — or point at a managed equivalent.
4. **MLX stays native and Mac-only.** Apple-GPU inference cannot run inside Linux containers.
   Apps reach it over OpenAI-compatible HTTP (already true). A graduated app flips one env var to
   a cloud OpenAI-compatible provider — no code change (already designed into `mlx-platform`).
5. **One thin SDK, two languages.** Follow `mlx-platform`'s rule: ship an SDK only where
   duplication is proven. It is proven now — `hub-gateway`, `pkg-llm-backend`, and every
   frontend's `apiClient.ts` are the same code re-invented.
6. **Config, not copies.** Kill `srt-flow`'s prod/staging directory duplication with env-based
   config. One canonical name per concept, one place to load it.
7. **Contracts over frameworks.** Apps depend on Postgres/S3/OIDC/OpenAI interfaces, never on the
   platform's internal implementation, so any app or provider can be swapped underneath.

## Non-goals

- Not Kubernetes, not multi-node, not high availability.
- Not a multi-tenant SaaS or a shared business-logic library.
- Not a container runtime for the apps themselves — apps stay native processes under
  `brbot-router` (fast startup, direct disk/GPU access).
- Not a secrets-management product (a lightweight secrets convention is in scope; a vault is not).
- Not a rebuild of `brbot-router` or `mlx-platform`.
- **Not a migration of the existing apps.** This plan changes no downstream repo. Converging
  `fr-hub-api`, `srt-flow`, `tk-orchestrator`, et al. onto the SDK is out of scope here; each is a
  separate, later, opt-in effort tracked in that repo, measured against the scorecard below.

## The workspace this platform serves

Findings from reviewing `brbot-router` and the 11 sibling projects. This is the duplication the
standard exists to let projects converge away from **when they choose to adopt it** — it is
motivation and a menu of opt-in targets, **not a work list this plan executes** on those repos.

| Reinvented N× | Where (motivating evidence) | What adopters can converge on |
|---|---|---|
| SQLite + WAL + single-writer + job queue (4×) | `fr-hub-api` (`SqliteWriter`), `tk-orchestrator` (SQLAlchemy), `srt-flow/pkg-job-orch`, `mlx-platform` records | **Shared Postgres + SDK job queue** |
| Filesystem-as-object-store (5+×) | `hub-api/.data`, `srt-api` JSON files, `srt-flow` `STORAGE_ROOT`, `mlx-audio`, `~/Public/*` | **MinIO (S3), bucket per project** |
| Homegrown auth (2 schemes + 1 header) | `hub-auth` (sessions), `srt-flow/pkg-auth` (Google OAuth/JWT), `X-MLX-Project` | **One identity service (deferred)** |
| Ruff + Pyright config copied verbatim (~25×) | every `pyproject.toml` | **Shared base tooling config** |
| Hand-rolled API/inference clients | `fr-hub-web`, `tk-web`, `hub-gateway`, `pkg-llm-backend` | **Published Python + TS SDK** |
| Per-app logging env + JSONL dirs | everywhere | **Loki/Grafana + shared log config** |

**Inconsistencies to reconcile:**

- The MLX gateway URL is **three different values** across consumers: `127.0.0.1:8933` (gateway),
  `192.168.0.12:9000` (`fr-hub-api`), `127.0.0.1:5900` (`srt-flow`). One canonical name and value.
- Env names diverge for one concept: `MLX_GATEWAY_URL` vs `MLX_PLATFORM_BASE_URL`; `HF_TOKEN` vs
  `HUGGING_FACE_HUB_TOKEN`; `API_PORT` vs `PORT` vs `--port`.
- No Dockerfile anywhere; `srt-flow` prod/staging is a full directory copy, not one deployable.
- `.env` files with secrets are committed in `mlx-audio`, `srt-flow`, `fr-hub-api`.

## Architecture

```text
                          Cloudflare (TLS + DNS: *.brettbot.ca, *.srt-flow.com)
                                        │
                                   cloudflared
                                        │
                          brbot-router  (host routing • lazy spawn • idle reap • dashboard)
                                │
                                ├─▶ app: fr-hub        (native process, sibling git repo)
                                ├─▶ app: srt-flow      (native process)
                                ├─▶ app: <new demo>    (scaffolded, auto-registered)
                                ├─▶ mlx-platform gateway (native, Apple-GPU, inference authority)
                                │
                                └─▶ mini-cloud-infra   (command unit — dashboard Start/Stop → make up/down)
                                        ├─ postgres     (one instance, database-per-project)
                                        ├─ minio        (S3, bucket-per-project)
                                        ├─ loki         (logs)
                                        ├─ prometheus   (metrics)
                                        ├─ grafana      (dashboards, one pane of glass)
                                        └─ identity     (deferred — OIDC/JWT when needed)

   Every app links the SDK:  config · db · storage · inference · observability · (auth later)
        The same docker-compose stack runs on a cheap VPS when an app graduates.
```

### Layers

- **L0 — Substrate & ingress (exists).** `brbot-router` is the single control plane: it
  co-supervises `cloudflared`, lazily spawns/reaps *app* processes by hostname, and carries
  `mini-cloud-infra` and `mlx-platform` as its own dashboard entries. The infra stack is a
  **`command`-kind** router entry — the dashboard's Start/Stop/Restart run `make up`/`make down`
  (detached `docker compose up -d`), so the router controls the stack's lifecycle without
  *parenting* it: compose detaches, the router never idle-reaps it, and it is deliberately **not**
  auto-started on boot (see *Control plane vs. data plane*). The container runtime under
  `mini-cloud-infra` is **Colima**, not Docker Desktop: Colima is a headless daemon that `make up`
  can start non-interactively (over SSH or from the dashboard's Start action) without a GUI login.
  Same choice holds on the VPS (or the distro's native Docker daemon).
- **L1 — Infra stack (new, docker-compose).** Postgres, MinIO, Loki, Prometheus, Grafana with
  persistent volumes and backups. Loopback-bound by default (opt-in LAN/tailnet exposure — see
  *Multi-machine development*); the *same file* runs on a VPS. **Resource-bounded:** this stack is
  always-on on the *same* Mac mini as MLX, which is memory-hungry for Apple unified memory. Each
  container gets an explicit memory limit in the compose file so the observability/data plane can
  never starve inference (the actual point of the box). Budget the standing footprint deliberately.
- **L2 — Platform services.** `mlx-platform` gateway (native, Mac-only). Identity service later.
  Each is an independent runtime service in its own repo, reached only over a wire contract.
- **L3 — SDK (new).** Small, **independently-versioned packages** (Python + TS), not one
  monolith. Consumers pin the versions they want, so a change to one package never forces every
  app to move (see *Decoupling model* below). Packages:
  - `config` — load canonical env; single source of truth for service URLs/names.
  - `db` — Postgres connection, migrations, and a **job-queue primitive** (replaces the 4 bespoke).
    The queue is the riskiest, most concurrency-sensitive package here, so its semantics are
    specified *before* `db` reaches a pinned `1.0` (see Phase 2): delivery guarantee (at-least-once
    via `SELECT … FOR UPDATE SKIP LOCKED`), visibility timeout, retry/backoff, dead-letter, and
    idempotency expectations on the consumer. Prefer an established Postgres-queue pattern (e.g.
    pgmq-style) over hand-rolling — the goal is to retire bespoke queues, not bless a 5th.
  - `storage` — S3/MinIO client, per-project bucket.
  - `inference` — thin OpenAI-client wrapper at the one canonical gateway URL (folds in
    `hub-gateway` + `pkg-llm-backend`).
  - `obs` — structured logging → Loki, metrics → Prometheus, correlation-ID propagation (already
    mandated by `mlx-platform`). Answers *"is the service healthy?"* — aggregated, no identity.
  - `analytics` — Mixpanel-style **product analytics** (per-person, timestamped events; funnels;
    retention) on the existing Postgres + Grafana. A **distinct concern** from `obs`: it answers
    *"did **this person** go upload → process → search → chat, and where did they drop off?"* —
    which needs an append-only per-person event store Prometheus deliberately can't hold. Reuses the
    existing boxes (no ClickHouse/Kafka stack); the client mirrors PostHog's
    `capture`/`identify`/`alias` so `MINI_ANALYTICS_BACKEND=posthog` is a later env-only graduation.
    Opt-in — showcased in `ref-showcase`, not an 8th scorecard gate.
  - `auth` — JWT verify helper (later).
- **L4 — Scaffolder (new).** `mini new <name> --type {fastapi|vite|node}` provisions
  DB + bucket, writes canonical `.env`, registers the `brbot-router` route + subdomain **through the
  router's `POST /routes` API** (the single registration write path — see *Route registration* and
  Phase 4/4.5; it falls back to a direct `projects.json` write only when the router is down, since
  then there is no live state to race), wires the SDK, adds a Grafana datasource/dashboard, and
  `git init`s. Because it does all that, the scaffolder is a **privileged actor** (Postgres admin,
  MinIO admin, control-plane write) — see its credential/least-privilege model under Phase 4.
- **L5 — App conventions.** Canonical env names, a port registry, `/healthz` + `/readyz`
  endpoints (so `brbot-router` readiness probes work uniformly), JSONL log format, and
  prod/staging via env rather than directory copies.

## Control plane vs. data plane

`brbot-router` is **not** replaced by this platform — it remains the control plane and dashboard,
and it now also fronts the infra stack. The router controls the stack's **lifecycle** (a Start /
Stop / Restart button on the dashboard) without becoming its **parent process** — the distinction
that matters. The lifecycles are opposite (infra is always-on and must be up before any app; the
router lazy-spawns and idle-reaps app processes per request), and the dependency runs one way (apps
depend on infra; the router depends on nothing in the data plane). If the always-on data plane were
parented by the churny control plane, a router redeploy or crash could cycle Postgres/MinIO
underneath live apps — so it must not be.

| | `brbot-router` (control plane) | infra stack (data plane) |
|---|---|---|
| Manages | app **processes** — start/stop/route/idle-reap, git-pull redeploy, dashboard, SSE status | stateful **services** — Postgres, MinIO, Grafana… |
| Lifecycle | lazy-spawn per request | always-on (started once, runs until stopped) |
| Run as | child processes of the router | detached `docker compose` containers — **not** router children |

The `command`-kind `projects.json` entry gives exactly this split. The router runs the entry's
`commands.start`/`commands.stop` (`make up`/`make down`) on demand from the dashboard, but:

- **Not parented** — `make up` runs `docker compose up -d`, so the containers detach and outlive
  the router. A router restart or crash leaves Postgres/MinIO/Grafana running.
- **Not idle-reaped, not auto-started on boot** — a `command` unit is never lazy-spawned,
  readiness-probed as a child, or reaped; even with `alwaysOn` set the router does **not** launch
  it at startup (so bringing the router up never triggers heavy tooling like Docker). Infra is
  brought up explicitly, once, via the dashboard Start action or `make up`.
- **Observed** — the router probes `healthPort` (Grafana's `13000`) and shows the stack as
  running/stopped, with `siteUrl` + `links` deep-linking to Grafana, Adminer, and the MinIO
  console — exactly as `mlx-platform`'s row deep-links to its `/console`.

```json
{ "name": "mini-cloud", "kind": "command", "path": "../mini-cloud/infra", "command": "true",
  "commands": { "start": "make up", "stop": "make down", "restart": "make restart" },
  "healthPort": 13000, "alwaysOn": true, "siteUrl": "http://<router-host>.local:13000" }
```

The `command` kind (observe-and-control, but don't own the process) is a sibling of the
**remote-upstream route type** from Phase 4.5 (no spawn, no idle-reap) — both are "the router
routes/controls but doesn't parent" entries; treat them as one shared idea rather than three.

## Decoupling model

The goal is that every component can evolve on its own. That comes from two mechanisms, kept
distinct:

- **Runtime independence (services).** Each service (`brbot-router`, `mlx-platform`, a future
  `identity`) runs as its own process, deploys independently, and talks to others **only over a
  wire contract** (HTTP, Postgres, S3) — never by importing another service's code. This is the
  decoupling that matters, and it already exists.
- **Version independence (packages).** The SDK is split into small packages, each **versioned
  independently**, and **consumers pin the versions they use**. App A can stay on `db@0.4` while
  App B moves to `db@0.5`; a change to `inference` never touches `storage`. Boundaries + semver +
  pinning — not separate repos — are what let packages evolve without coupling.

Rules:

- Services communicate over contracts, never shared imports across a service boundary.
- Each SDK package has its own version and its own dependency set; apps pin, and upgrade on their
  own schedule.
- Infra services are decoupled by pinned image tags in the compose file (a Postgres bump never
  touches MinIO).

## Repository & disk organization

Add exactly **one new sibling repo, `mini-cloud/`** — a workspace of independently-versioned
packages (the same pattern your `fr-hub-api` and `srt-flow` workspaces already use successfully).
Do **not** reshuffle the existing repos.

```text
/Users/brett-m1/Documents/GitHub/
├── brbot-router/          ← control plane / dashboard (extended: route API + infra command entry)
├── mlx-platform/          ← unchanged (inference service, own repo)
├── mini-cloud/              ← NEW (one repo, workspace)
│   ├── infra/             # docker-compose.yml + per-service config, backup script
│   ├── packages/          # each package versioned independently, pinned by consumers
│   │   ├── config/
│   │   ├── db/
│   │   ├── storage/
│   │   ├── inference/
│   │   └── obs/
│   ├── scaffolder/        # `mini new` + `mini score` CLI (own version)
│   ├── templates/         # app skeletons: fastapi / vite / node (each scores 7/7 by default)
│   ├── examples/          # in-repo reference apps (ref-fastapi, ref-vite) — SDK proof + regression
│   └── docs/              # this doc, ADRs, env-name + port registry, adoption guide (+ MLX_PLATFORM_*.md)
├── fr-hub-api/ fr-hub-web/ srt-flow/ …   ← all unchanged
```

Rationale and rules:

- **Name.** The repo/folder is `mini-cloud`; the CLI verb is `mini` (`mini new`), the SDK scope is
  `@mini-cloud/…`, the canonical inference env is `MINI_INFERENCE_URL`, and the router/infra entry
  is `mini-cloud-infra`. One name family, fixed in Phase 0 before it's baked into commands, env,
  and SDK imports (renaming later means chasing all four).
- **Genuine long-lived runtime services get their own repo** (like `mlx-platform`); libraries and
  tools live in the `mini-cloud/` workspace. A future `identity` service is its own repo.
- **Data never lives in the repo tree.** The infra stack uses Docker **named volumes**, so
  `mini-cloud/infra/` holds only config; Postgres/MinIO bytes live in Docker-managed volumes that
  back up cleanly and recreate identically on a VPS.
- **Keep the flat sibling layout — do not regroup into `apps/`, `libs/`, `tools/`.** Three
  things hardcode the current paths and a physical move means chasing all of them:
  `brbot-router/projects.json` (`../foo` resolution), `~/.cloudflared/config.yml`, and
  cross-project absolute paths (`srt-api` → `yt-down/bin`, `local-tube` `MEDIA_ROOT`, the shared
  `fr-hub-api/.data/artifacts`). Logical grouping, if wanted, comes free from a root `README.md`
  index — not from moving bytes.
- Once `mini-cloud/docs/` exists, move the loose `MINI_CLOUD_ARCHITECTURE.md` and the two
  `MLX_PLATFORM_*.md` files into it so the design docs have a home.

## Cloud graduation path

When a demo matures, it graduates to its own cheap VPS with no application rewrite:

1. Run the same `mini-cloud-infra` docker-compose on the VPS (or point at managed Postgres/S3).
2. Set the app's canonical env to the VPS service URLs.
3. Move the app's DNS/route off `brbot-router` to the VPS (Cloudflare record change).
4. MLX-backed features switch to a cloud OpenAI-compatible provider via one env var.

The app changes nothing in code because it only ever spoke Postgres/S3/OpenAI/OIDC over config.

## Multi-machine development

By default every infra service binds to loopback (`127.0.0.1`) on the infra host ("Machine A"). A
second machine ("Machine B") can develop against the shared infra. This widens the trust boundary
from a single trusted box to the network, so it is strictly opt-in and changes the security
posture.

**Transport — prefer a tailnet.** Prefer a WireGuard/Tailscale tailnet over raw LAN binding: it
gives authenticated, encrypted access with the *same* model whether Machine B sits on the LAN or
across the internet — which mirrors the VPS graduation story. Bind services to the tailnet
interface. Raw LAN binding is the simpler fallback for an all-trusted home LAN, at the cost of
plaintext Postgres/MinIO traffic on the wire.

**Opt-in bind + mandatory auth.** `INFRA_BIND_ADDR` (default `127.0.0.1`) selects the bind
interface in `infra/docker-compose.yml`. The moment it moves beyond loopback, loopback trust ends:
Postgres must use `scram-sha-256` and MinIO real access keys — a hard prerequisite, not a
follow-up. Bind to a *specific* interface IP, never `0.0.0.0`.

**Host addressing.** On an all-Mac network use mDNS (`machine-a.local`) — no static DHCP
reservation needed; a fixed LAN IP is the fallback.

**Workflow (a) — remote infra, local dev, no route (the default).** Machine B runs `mini new` with
its `config` pointed at Machine A (`DATABASE_URL`, `STORAGE_ENDPOINT`, `MINI_INFERENCE_URL`). The
scaffolder provisions the DB + bucket remotely over the wire and the app runs on Machine B's own
dev server. Works as soon as the bind/auth change lands — no `brbot-router` involvement.

**Workflow (b) — full `*.brettbot.ca` subdomain.** This needs more than a remote `projects.json`
write, because **`brbot-router` lazy-spawns app processes from local disk paths** — an app that
lives on Machine B's disk cannot be spawned by Machine A. So (b) requires `brbot-router` to gain a
new **remote-upstream route type**: proxy `app.brettbot.ca` → `machine-b.local:PORT` with no spawn
and no idle-reap. Registration goes through a small authenticated API on `brbot-router`
(`POST /routes`), not an SSH write, so the router validates and applies to its own live state +
`projects.json` rather than racing an out-of-band edit.

**Route registration is one write path, local *and* remote (approach A).** `POST /routes` is not
special to workflow (b): it is the *single* way the scaffolder registers **any** route from Phase 4
onward — a local `mini new` posts a `kind:"app"` entry, a Machine-B `mini route add` posts a
`kind:"remote"` one; the router owns `projects.json` and never has it edited out from under its
running state. The one carve-out is resilience, not a second mechanism: when the router is **down**
(no live state to race), the local scaffolder falls back to writing `projects.json` directly, which
the router loads on next start. So the control plane is touched exactly once — to add the API +
remote kind (Phase 4.5) — and every later registration reuses it.

## The adoption standard (what downstream repos adopt later)

This plan's deliverable-that-touches-other-repos is **a standard, not a diff**. mini-cloud publishes
it; each repo adopts on its own schedule, and its owner runs the adoption. Two artifacts define it:
the **SDK/workflow contract** below, and the **scorecard** after it.

### SDK + workflow contract

A project is "on the standard" when it satisfies this contract. `mini new` emits it by default; an
existing repo reaches it by following the *adoption workflow*.

- **Canonical config.** Read service URLs/names only through the `config` package (or the documented
  env names it defines) — never hardcode `127.0.0.1:8933` / `:9000` / `:5900`. One name per concept
  (`MINI_INFERENCE_URL`, `DATABASE_URL`, `STORAGE_ENDPOINT`).
- **Data through the SDK.** Persistence via `db` (Postgres + migrations + job queue); blobs via
  `storage` (per-project bucket). No bespoke SQLite writer, no filesystem-as-object-store.
- **Inference through the SDK.** Calls go through `inference` at the one canonical gateway URL.
- **Observability by default.** Structured logs → Loki and metrics → Prometheus via `obs`, with
  correlation-ID propagation, visible in the shared Grafana.
- **Standard repo surface.** The task entrypoints, validation harness, lint/format gates, agent repo
  map, and structured docs required by the scorecard (below).
- **Pinned, opt-in versioning.** The repo pins each SDK package version and upgrades on its own
  schedule (per *Decoupling model*). Adopting one package (e.g. `obs`) does not require adopting
  the rest.

### Adoption workflow (run by the repo owner, later)

A documented, self-service sequence — mini-cloud provides the guide and the checker; it does not run
this on anyone's repo:

1. **Score.** Run the scorecard checker against the repo to get a 0–7 baseline.
2. **Wire config.** Introduce the `config` package / canonical env names; delete divergent ones.
3. **Adopt incrementally.** Pull in `obs`, then `storage`/`db`, then `inference` — one pinned
   package at a time, re-scoring after each. Each step is independently shippable.
4. **Meet the repo surface.** Add the standard task entrypoints, validation harness, lint gates,
   agent repo map, and docs structure.
5. **Re-score to 7/7** and record the result. Graduation-readiness (VPS env/DNS switch) falls out
   for free once config + SDK are in place.

## Scorecard — repo readiness

Every repo the standard produces or that adopts it is measured on a **seven-metric scorecard**
(pass/fail each; target 7/7). It doubles as an *agent-readiness* score: a repo that passes is one an
autonomous agent can bootstrap, navigate, change, and validate without tribal knowledge. A fresh
`mini new` app scores 7/7 out of the box; the in-repo reference app must hold 7/7 as SDK regression
protection; existing repos use it as their adoption baseline and target.

| # | Metric | Passes when… | How the standard delivers it |
|---|---|---|---|
| 1 | `bootstrap_self_sufficiency` | a fresh clone comes up with **one documented command** — deps pinned, env seeded from `.env.example`, no tribal steps | template ships `make setup` / `mini bootstrap`, pinned lockfiles, `.env.example` |
| 2 | `task_entrypoints` | every common task (run, test, lint, migrate, seed) is a **named, stable entrypoint** with the *same name across all repos* | shared Makefile/justfile targets in every template |
| 3 | `validation_harness` | **one command** runs the full check suite (tests + typecheck + healthcheck) and exits non-zero on failure — usable by human, CI, and agent, against an **ephemeral/throwaway DB** so it isn't coupled to the always-on stack being reachable | `make check` wired to the SDK's `/healthz`+`/readyz` and test runner, pointed at a disposable Postgres (container or temp schema) |
| 4 | `lint_format_gates` | shared ruff/pyright/prettier config, **one command to check and one to fix**, identical everywhere | Phase-0 shared base tooling config referenced by every `pyproject.toml`/template |
| 5 | `agent_repo_map` | a **machine-readable map** (e.g. `AGENTS.md`) states where things live, the entrypoints, and the conventions, so an agent navigates without guessing | template ships a generated `AGENTS.md`; scaffolder keeps it current |
| 6 | `structured_docs` | docs sit in **predictable canonical locations** (`README` + `docs/` + ADRs + env/port registry), not scattered prose | template docs skeleton + the Phase-0 env-name/port registry |
| 7 | `observability_wired` | the app emits structured logs **and** metrics **by default** (no opt-in flag), with correlation-ID propagation, and they're visible in the shared Grafana | `obs` SDK package wired into every template's request path; scaffolder provisions the Grafana datasource/dashboard |

## Roadmap

Staged like the MLX platform: each phase is independently shippable and has completion criteria.
Phases 0–2 are the critical path; the scaffolder (Phase 4) is the headline feature but depends on
there being services worth scaffolding.

**Every phase below builds only `mini-cloud`.** Where a phase needs to prove an SDK or template
works, it does so against a **reference app that lives inside `mini-cloud`** (`examples/`), never by
editing a downstream repo. Adoption by real apps is a separate, per-repo, opt-in effort — the
*outputs* this plan produces to make that possible are the SDK, the templates, the adoption
workflow, and the scorecard (below).

### Phase 0 — Foundations & conventions (no migrations)

- ADR: platform boundaries and ownership (DB, storage, auth, inference, ingress, observability).
- **Canonical env-var names + a port registry.** Pick one name per concept (e.g.
  `MINI_INFERENCE_URL`, `DATABASE_URL`, `STORAGE_ENDPOINT`) and end the 8933/9000/5900 split.
- Shared base tooling config (ruff/pyright/pytest) referenced by every `pyproject.toml`.
- **Define the seven-metric scorecard** (above) as the written readiness standard.
- **Pick the container runtime: Colima** (headless — not Docker Desktop, whose daemon is
  GUI/login-gated). Colima's daemon can be started non-interactively by `make up` (over SSH or from
  the router dashboard's Start action) without a GUI login. This is a Phase-0 choice because the
  always-on data plane depends on a runtime that comes up without a desktop session.
- Create the `mini-cloud/` repo holding this doc, the compose stack, the SDK, and the scaffolder.

*Done when:* one documented naming/port standard and the scorecard definition exist, the container
runtime is Colima, and the base config is consumed by at least the in-repo reference app.

### Phase 1 — Infra stack up (docker-compose)

- Postgres, MinIO, Loki, Prometheus, Grafana in one compose file, persistent volumes, a backup
  job. Registered in `brbot-router` as a **`command`-kind** entry whose Start action runs `make up`
  — which brings up **Colima** first, then `docker compose up -d`, so the daemon exists before the
  stack starts and the containers detach rather than becoming router children (see *Control plane
  vs. data plane*).
- Grafana provisioned with Loki + Prometheus datasources; MinIO console reachable; Postgres on
  loopback.
- **Bounded retention (mandatory, not backlog).** Loki and Prometheus are unbounded by default and
  will silently fill the boot disk — which takes down *everything*, including MLX. Set explicit
  size/time retention caps for both, and a disk-usage alert, in this phase.
- **Backup *and* restore.** The backup job (Postgres dump + MinIO snapshot on a schedule) is only
  half of it: a documented, tested **restore** into a clean volume is part of Phase 1. An untested
  backup is not a backup.
- **Opt-in LAN/tailnet exposure (default off).** `INFRA_BIND_ADDR` selects the bind interface; any
  non-loopback value requires Postgres `scram-sha-256` + MinIO access keys as a hard prerequisite
  (see *Multi-machine development*).

*Done when:* `docker compose up` brings the whole stack, Grafana renders, a DB and bucket can be
created by hand, Loki/Prometheus retention caps are enforced, a backup has been **restored** into a
clean volume at least once, and the same file boots on a spare Linux box.

### Phase 2 — SDK v0 + reference app (Postgres · storage · observability)

- Python SDK: `config`, `db` (connection + migrations + job queue), `storage`, `obs`.
- Prove it against an **in-repo reference app** (`examples/ref-fastapi`), *not* a downstream
  project: the reference app runs on shared Postgres + MinIO, uses the SDK job queue, and emits
  logs/metrics to Grafana. It doubles as the fastapi template's seed and as the SDK's living
  documentation.

*Done when:* the reference app runs on shared Postgres + MinIO through the SDK, emits logs/metrics
to Grafana, uses the SDK job queue — and satisfies the scorecard (below). The job queue's semantics
(delivery guarantee, visibility timeout, retry/backoff, dead-letter) are written down and covered by
tests before `db` is pinned `1.0`, since consumers pin it and inherit those semantics. No downstream
repo is touched.

### Phase 3 — Inference SDK module

- Build the SDK `inference` module: a thin OpenAI-client wrapper at the one canonical gateway URL,
  designed to be a drop-in successor to the `hub-gateway` / `pkg-llm-backend` patterns so an adopter
  *can* fold theirs into it later. Prove it from the reference app.

*Done when:* the reference app calls inference through the SDK `inference` module at the single
canonical URL, and the adoption guide documents how a project replaces a hand-rolled client with
it. (No downstream client is rewritten here.)

### Phase 4 — Scaffolder (`mini new`)

- Generate the backend/frontend skeleton, provision DB + bucket, register the `brbot-router` route +
  subdomain **via `POST /routes`** (approach A: the router owns `projects.json`, so registration
  goes through its API when it is running and falls back to a direct file write only when it is
  down), write canonical env, wire the SDK + a Grafana dashboard.
- **Single registration write path.** Because Phase 4.5 must touch the control plane anyway, its
  `POST /routes` API is built **once, here in Phase 4**, and both local (`kind:"app"`) and remote
  (`kind:"remote"`) registration reuse it — rather than shipping a `projects.json`-editor now and
  replacing it in 4.5. `POST /routes` is idempotent on `name` and persists the accepted entry back
  to `projects.json` so it survives a router restart.
- **`--type vite` is backend-complete only until Phase 5.** The TS SDK ships in Phase 5, so a
  `vite` app scaffolded here wires config/routing/Grafana but cannot hit 7/7 (observability +
  typed client) until the TS SDK lands. The fastapi path is fully 7/7 in this phase.
- **Scaffolder credential model.** `mini new` holds Postgres-admin, MinIO-admin, and control-plane
  write authority, so decide *where those creds live* and *how scoped* they are: it should create a
  **per-project least-privilege role/access-key** for the generated app (not hand the app the admin
  creds it used to provision), and its own admin creds live outside any repo. In remote workflow
  *(a)* it exercises this authority across the wire, so the same scoping applies there.
- Verify **remote scaffolding** (workflow *a*): `mini new` run from Machine B against Machine A's
  infra provisions DB + bucket over the wire. Document the required client env.

*Done when:* `mini new demo-x --type fastapi` yields a running, routed app at
`demo-x.brettbot.ca` with DB, bucket, logging, and metrics already wired — zero manual steps — and
the generated app **scores 7/7 on the scorecard**.

### Phase 4.6 — Scorecard checker + adoption guide

- Ship a **scorecard checker** (`mini score <repo>`) that scores any repo 0–7 against the seven
  metrics, and a written **adoption workflow** guide (the sequence in *The adoption standard*).
- These are the only outputs aimed at existing repos — a self-service path their owners run. This
  plan still edits no downstream repo.

*Done when:* `mini score` reports 7/7 for a freshly scaffolded app and a truthful lower score for an
unmodified legacy repo, and the adoption guide is published in `docs/`.

### Phase 4.5 — Remote route registration (multi-machine)

> Under **approach A**, the control plane is touched **once**: the authenticated `POST /routes`
> write API lands in Phase 4 (used for local `kind:"app"` registration), and Phase 4.5 is the
> increment that extends it with the **`kind:"remote"` upstream type** for cross-machine routing.
> The two are one coherent change to `brbot-router`, not two mechanisms. This generalizes the
> `external` (no-spawn/no-reap) entry kind introduced for the infra dashboard row in Phase 1.

- `brbot-router` gains a **remote-upstream route type** (`kind:"remote"`: proxy to a configurable
  `upstreamHost:PORT`, no spawn, no idle-reap, no git/redeploy) on top of the `POST /routes` API
  from Phase 4, so an app on Machine B can claim a `*.brettbot.ca` subdomain (workflow *b*).
- `mini route add <name> --domain … --upstream-host … --port …` (and `mini route remove`) posts a
  remote entry to Machine A's router; there is **no file fallback** for remote (Machine A's
  `projects.json` isn't on Machine B's disk), so the API must be reachable and the command fails
  loudly otherwise. The API is gated by its own `ROUTE_REGISTRATION_TOKEN` (default off) and is
  scoped to the dashboard host so it never shadows an app's own `/routes` path.
- **DNS is separate.** Claiming the route wires the proxy; the `*.brettbot.ca` Cloudflare record is
  still a manual/out-of-band step (the router doesn't manage DNS).

*Done when:* an app running on Machine B is reachable at `app.brettbot.ca` via a route registered
remotely, with no manual `projects.json` edit on Machine A.

### Phase 5 — TS SDK + frontend template

- `@mini-cloud/sdk` for Vite/React apps (typed API client, config, later auth). The scaffolder
  emits it for `--type vite`; prove it with an in-repo `examples/ref-vite` reference frontend.

*Done when:* a freshly scaffolded frontend (and the reference frontend) talk to their backend via
the shared client, with no hand-rolled `apiClient.ts` — validated in `mini-cloud`, not in any
downstream web app.

### Phase 6 — Identity service (only if/when needed)

- Stand up a platform identity service issuing OIDC-style JWTs, plus the SDK `auth` verify helper,
  modeled on the `srt-flow/pkg-auth` pattern. Prove it end-to-end with the reference app(s). It is
  the *target* an adopter later points at to retire `hub-auth` / `X-MLX-Project`-as-auth — that
  retirement happens in those repos, not here.

*Done when:* the reference app authenticates against the identity service and the gateway trusts
its JWT, and the adoption guide documents the switch-over for an existing app.

### Phase 7 — Graduation playbook + analytics

- Document and script the VPS graduation (Phase-1 compose on the VPS, env repoint, DNS move).
- **Analytics — built** as the `mini-cloud-analytics` SDK package rather than a self-hosted PostHog
  deployment: per-person event capture, funnels, and retention on the **existing** Postgres +
  Grafana (a separate `analytics` DB via `MINI_ANALYTICS_DSN`, a read-only Grafana Postgres
  datasource, package-owned migrations). The heavy ClickHouse/Kafka/Redis/Zookeeper PostHog stack
  was rejected as antithetical to the Mac-mini "small services" grain. The client is
  PostHog-compatible, so `MINI_ANALYTICS_BACKEND=posthog` remains the env-only seam to ship to real
  PostHog if a maturing app outgrows the Postgres store. Showcased end-to-end in `ref-showcase`
  (4-step funnel, `/analytics/*` tour, seeded stream, Grafana dashboard). See
  [`analytics-plan.md`](analytics-plan.md).

*Done when:* the reference app runs on a VPS off the same compose stack with only env/DNS changes,
documented end to end as a repeatable playbook adopters can follow.

### Cross-cutting backlog

All of these are **conventions the standard defines and the templates ship with** — they are not
edits applied to existing repos.

- **Secrets hygiene:** the templates ship a `.env.example` + untracked `.env` convention (and later
  a simple secrets store); the scorecard checks for it. (Cleaning up already-committed `.env` files
  is each repo's own opt-in adoption task.)
- **Backups:** Postgres dump + MinIO snapshot on a schedule (a `mini-cloud` infra concern).
- **Env-over-copies deployment:** the templates model prod/staging as one env-switched deployable,
  giving adopters a pattern to collapse directory-copy setups onto later.

## Platform acceptance criteria

The platform is complete as a prototype factory when — measured **entirely within `mini-cloud`**,
with no downstream repo required to have changed:

- The infra stack comes up from one compose file and runs identically on the Mac mini and a VPS.
- `mini new` produces a running, routed, DB + bucket + logging + metrics-wired app with no
  manual steps, and that fresh app **scores 7/7 on the scorecard out of the box**.
- The SDK provides a job queue, an inference client, and shared ruff/pyright config, so an adopting
  project *can* drop its bespoke versions — proven by the in-repo reference app carrying none.
- There is exactly one canonical **name** for each shared service URL. Values are environment-
  swappable by design — `MINI_INFERENCE_URL` in particular flips to a cloud OpenAI-compatible
  provider on graduation — so the `config` package treats them as per-environment, not hardcoded
  constants. "One canonical value" holds *within* an environment, not across the local→VPS move.
- The reference app's logs and metrics are visible in one Grafana instance.
- The reference app graduates to a VPS with only env and DNS changes — no code rewrite.
- The **adoption workflow and scorecard are published**, so any existing repo has a documented,
  self-service path to converge on the standard on its own schedule.
