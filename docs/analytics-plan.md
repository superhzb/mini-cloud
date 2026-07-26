# Plan: `mini-cloud-analytics` — Mixpanel-style product analytics on Postgres + Grafana

> Status: **built** (2026-07-26). The `mini-cloud-analytics` package, the `ref-showcase` 4-step
> funnel + `/analytics/*` tour + seeded stream + Grafana dashboard, and the infra `analytics-init` /
> read-only Postgres datasource all ship and are verified end-to-end against the live stack. Companion
> to [`MINI_CLOUD_ARCHITECTURE.md`](MINI_CLOUD_ARCHITECTURE.md) and
> [`ref-showcase-plan.md`](ref-showcase-plan.md). All four open decisions below were taken as
> recommended (shared DB, background drop-on-overflow flush, explicit events, query-time identity).

## Positioning — a new capability, not a tweak to `obs`

`mini-cloud-obs` answers *"is the service healthy?"* — aggregated counters/gauges/logs, no
identity. Product analytics answers *"did **this person** go upload → process → search → chat, and
where did they drop off?"* That requires an **append-only, per-person, timestamped event store** —
something Prometheus deliberately can't hold. So this is a distinct SDK package that **reuses the
existing Postgres + Grafana boxes** (no new heavy containers — the reason we chose this over
self-hosted PostHog, whose ClickHouse + Kafka + Redis + Zookeeper stack is antithetical to the
Mac-mini "small services" grain).

The SDK deliberately mirrors PostHog's `capture` / `identify` / `alias` shape so a maturing demo
can later flip `MINI_ANALYTICS_BACKEND=posthog` and ship to real PostHog **by changing env, not
code** — the platform's graduation thesis, and a clean answer to the original PostHog question.

### `obs` vs `analytics` — the boundary

| | `mini-cloud-obs` | `mini-cloud-analytics` |
|---|---|---|
| Question | Is the service healthy? | What did this user do? |
| Shape | Aggregated counters/gauges/logs | Per-person timestamped events |
| Identity | None | `distinct_id` / person |
| Store | Prometheus + Loki | Postgres event store |
| Audience | SRE / ops | PM / growth |

## Architecture decisions (recommended defaults — flagged as open below)

1. **One shared `analytics` Postgres database** (a new platform data store on the existing `:15432`,
   not a new port), rather than per-project events. Mixpanel *is* one event warehouse; a shared
   store makes cross-project product analytics possible. Events are tagged with `project` /
   `app_name`. Per-project fallback stays possible by pointing `MINI_ANALYTICS_DSN` at the app's
   own DB.
2. **Background batched flush, drop-on-overflow.** `capture()` never blocks the request path; a
   bounded in-process buffer flushes on size/interval and at shutdown, and increments an `obs`
   counter when it drops — honest backpressure, same as the real PostHog client.
3. **Explicit events only** for v0 (no auto-pageview middleware). Provide a small per-request
   context helper to bind `distinct_id` / `session_id` / correlation-id; product events should be
   intentional.
4. **Scorecard stays 7/7.** Analytics is opt-in — not every demo needs it — so it's showcased in
   `ref-showcase` and documented in the adoption guide, not added as an 8th gate.

## The build

### Package `mini-cloud-analytics` (`packages/analytics/`, namespace `mini_cloud.analytics`)

Mirror `obs`'s layout exactly (hatchling, `py.typed`, `[tool.ruff] extend = "../../tooling/ruff-base.toml"`,
per-package pytest, distribution `mini-cloud-analytics` v0.1.0). The root `members = ["packages/*"]`
glob auto-discovers it, **but** root `pyproject.toml`'s `[tool.uv.sources]` lists each package
explicitly for in-workspace resolution — so add one line, `mini-cloud-analytics = { workspace = true }`.
(This is a discovery-vs-resolution distinction: the glob finds the package; the sources block resolves
its editable path. Every existing package has both.) That single line is the only root edit.

- **Deps:** `mini-cloud-config` (bottom of graph) + `mini-cloud-db` (pool for the default sink).
  Soft-imports `mini_cloud.obs.correlation` for correlation-id stitching (not a hard dep).
  **Keep the core import dependency-light, `obs`-style:** `obs` pushes starlette into an optional
  `[asgi]` extra so the core imports clean. Mirror that — `posthog-python` goes into an optional
  `[posthog]` extra (only the graduation seam needs it), not the base deps.
- **Client API (PostHog-compatible):**
  - `Analytics.capture(distinct_id, event, properties=None, timestamp=None)`
  - `Analytics.identify(distinct_id, properties=None)` — upsert person
  - `Analytics.alias(previous_id, distinct_id)` — stitch anonymous → identified
  - `Analytics.from_settings(settings)` constructor (like `InferenceClient`)
  - `flush()` / `close()` for shutdown
- **`EventSink` protocol** with `PostgresSink` (default). `PostHogSink` is the documented
  graduation seam (thin `posthog-python` wrapper behind the `[posthog]` extra, stubbed in v0).
  Selected by `MINI_ANALYTICS_BACKEND` = `postgres` (default) | `posthog`.

**Identity resolution — the one genuinely hard part, decided here so it doesn't leak into the hot
path.** `capture()` writes events with the raw `distinct_id` only; it does **not** look up or upsert
`person_id` per event (that would defeat the batched flush and add a read to the write path). Person
resolution is deferred to **query time**: funnel/retention SQL joins events through
`analytics_person_aliases` to collapse anonymous → identified. `identify()`/`alias()` write the
person + alias rows; the event stream stays append-only and dumb. (`analytics_events.person_id` is
therefore nullable and best treated as a denormalized cache to backfill later, not a write-path
requirement.)

### Event-store schema (SDK-owned migrations, applied via `mini-cloud-db` migrate)

**New pattern to establish:** no package ships and applies its *own* migrations today — ref-showcase
owns migrations as an *app*, against its own DB. Here the package ships a `migrations/` dir and the
consumer calls `migrate(analytics_pool, <package_migrations_path>)` against a **separate** DSN
(`MINI_ANALYTICS_DSN`), not the app's own DB. `migrate()`/`make_pool()` support this as-is; it's just
more wiring than the single-DB ref-showcase case, and it's the first instance of the pattern.

- `analytics_events` — `id bigserial`, `event`, `distinct_id`, `person_id` (**nullable** — resolved
  at query time, see identity note above), `project`,
  `session_id`, `properties jsonb`, `timestamp timestamptz`, `received_at`, `correlation_id`.
  Indexes on `(project, event, timestamp)` and `(distinct_id, timestamp)`.
- `analytics_persons` — `person_id`, `distinct_ids text[]`, `properties jsonb`, `first_seen`,
  `last_seen`.
- `analytics_person_aliases` — anonymous → identified map. (Monthly partitioning noted as future
  work.)

### Config + registry (`mini-cloud-config`, `docs/env-and-ports.md`, `.env.example`)

Add canonical env. Each field is **four** edits, not three — the loader is not reflection-driven:
(1) a field on the frozen `Settings` dataclass; (2) a `_FIELD_TO_ENV` entry; (3) a
`field=get("field")` line in the `load_settings()` constructor (the spot easy to miss);
`CANONICAL_ENV_KEYS` is auto-derived as `tuple(_FIELD_TO_ENV.values())`, so it needs no manual edit.

- `MINI_ANALYTICS_DSN` — Postgres DSN to the shared `analytics` DB (loopback default)
- `MINI_ANALYTICS_BACKEND` — `postgres` | `posthog` (default `postgres`)
- `MINI_ANALYTICS_PROJECT` — defaults to `APP_NAME`, resolved in the client's `from_settings`
  (`project or settings.analytics_project or settings.app_name`), exactly like `InferenceClient` —
  the default lives at the consumer, not in `config` itself.

No new port — the `analytics` DB shares Postgres `:15432`.

### Infra — the heaviest part of the plan, and all net-new ground

Unlike the SDK layer (which cleanly copies `obs`/`db`/`config` precedent), **none of the infra
pieces below exist yet** — budget accordingly.

- New `make -C infra analytics-init` — create the `analytics` DB, a **read-only Grafana role**,
  apply schema. This is **not** a variant of `create-project.sh`: that script creates a full *owning*
  LOGIN role, applies no schema, and wires no datasource. Read-only role + schema-apply are both new.
  For the read-only role, grant `SELECT` + `USAGE` and set `ALTER DEFAULT PRIVILEGES` so future
  tables/partitions (see the monthly-partitioning note) stay readable without a re-grant.
- **Grafana Postgres datasource** provisioning file → the `analytics` DB (read-only). **This is the
  first Postgres datasource in the stack** — `provisioning/datasources/datasources.yml` currently
  provisions only Prometheus and Loki. It must land *before/with* the dashboard, or the dashboard's
  panels reference a datasource UID that doesn't exist and render empty.
- **Dashboard copy is NOT free via the existing `project` target.** That target hardcodes copying a
  single `../examples/<NAME>/grafana/dashboard.json` → `config/grafana/dashboards/app-<NAME>.json`.
  A second `grafana/analytics-dashboard.json` **will silently not be copied** without editing the
  `project` target (or `create-project.sh`) to copy the additional file. Either extend the copy to
  glob `grafana/*.json`, or drop the analytics dashboard straight into
  `config/grafana/dashboards/` (the file provider auto-loads that dir).
- A reusable **funnel/retention SQL library** shipped in the package + docs so apps don't reinvent
  it (funnels via `min(timestamp) filter (where event=…)` per `distinct_id`; cohort SQL for
  retention). Funnel/retention joins resolve identity through `analytics_person_aliases` at query
  time, per the identity-resolution decision above.

## Showcasing in `ref-showcase` (the canary)

The Document Intelligence domain already has the ideal user-driven flow. Instrument a real 4-step
funnel:

1. `document_uploaded` — `POST /documents`
2. `document_processed` — worker, on pipeline completion
3. `search_performed` — `POST /search`
4. `chat_started` — `POST /documents/{id}/chat`

Plus `identify` a demo user and `alias` anonymous → identified on a login-ish endpoint.

- **`/analytics/*` tour endpoints:** `POST /analytics/capture` (manual event),
  `GET /analytics/funnel` (runs funnel SQL → conversion), `GET /analytics/events` (recent stream) —
  exercises the whole SDK surface.
- **Seed:** extend `seed.py` to emit a deterministic multi-user event stream offline (mirrors
  `make seed`), so funnel/retention dashboards have data with no live gateway.
- **Grafana:** author `grafana/analytics-dashboard.json` (funnel conversion, DAU/WAU, top events,
  retention cohort). **Not** copied for free by the current `project` target (it copies only a single
  `dashboard.json`, see Infra) — reach it via the glob/drop-in fix above, and its panels bind to the
  new Postgres datasource UID.
- **Coverage gate:** extend `test_sdk_surface_gate.py` by adding `"mini_cloud.analytics"` to its
  hardcoded `TOP_LEVEL_PACKAGES` tuple. The gate is AST-resolved and strict, so this then **forces**
  every analytics `__all__` symbol and public method to be referenced in `ref_showcase/src` — that's
  what makes the `/analytics/*` tour endpoints mandatory rather than optional. If analytics grows a
  public submodule (like `obs.asgi`), it must also be added to the gate's submodule allowlist.
- **Tests:** offline unit tests use a fake sink (no DB); live tests write to a real `analytics` DB,
  gated by `--run-live` + `MINI_ANALYTICS_DSN` — consistent with the existing per-package /
  live-gated convention.
- **Docs:** extend `docs/service-tour.md`, README, AGENTS with the analytics tour.

## Sequencing

**Phase A — SDK + schema + infra**

1. Scaffold `packages/analytics/` (pyproject, namespace, py.typed, README) + the one-line root
   `[tool.uv.sources]` entry.
2. Add the three config fields (four edits each: `Settings` + `_FIELD_TO_ENV` + `load_settings()`
   constructor line) + registry + `.env.example`.
3. Event-store migrations (package-owned `migrations/` dir, applied against `MINI_ANALYTICS_DSN`) +
   `PostgresSink` + `Analytics` client (background batched flush, query-time identity resolution) +
   offline unit tests (fake sink).
4. Infra — the heavy step: `analytics-init` (DB + **read-only** role + schema), the **first**
   Grafana Postgres datasource, dashboard-copy fix, funnel/retention SQL library.

**Phase B — showcase in ref-showcase**

5. Wire `Analytics` into `resources.py`; instrument the 4-step funnel across `app.py` /
   `pipeline.py` / `worker.py` / `search.py`.
6. `/analytics/*` tour endpoints.
7. Deterministic seed event stream + Grafana analytics dashboard (reached via the dashboard-copy
   glob/drop-in fix, bound to the new Postgres datasource — not the default single-file copy).
8. Extend coverage gate + service-tour / README / AGENTS; offline unit + live tests.

**Phase C — platform docs + graduation seam**

9. Architecture doc + adoption-guide updates; document the `MINI_ANALYTICS_BACKEND=posthog` seam
   (stub `PostHogSink`). Confirm scorecard stays 7/7.

## Open decisions to confirm before building

- **Shared `analytics` DB** vs per-project events — recommend shared (one warehouse).
- **Background / drop-on-overflow flush** vs simple sync insert — recommend background.
- **Explicit events only** vs auto-capture middleware for v0 — recommend explicit.
- **Identity resolution: query-time** (join through aliases) vs write-time (`person_id` lookup per
  `capture`) — recommend query-time, so the batched write path stays a dumb append. Decided above;
  listed here as the load-bearing choice reviewers should sign off on explicitly.
