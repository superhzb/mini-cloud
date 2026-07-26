# Plan: `examples/ref-showcase` — an exhaustive SDK showcase app

> Status: **planned, not built** (as of 2026-07-25). Companion to
> [`MINI_CLOUD_ARCHITECTURE.md`](MINI_CLOUD_ARCHITECTURE.md). This is a design/build plan for a
> second in-repo reference app; it edits no downstream repo.

## Goal & positioning

`ref-fastapi` is deliberately *minimal* — one notes→summary flow that touches each package once,
sized to be the fastapi template seed and hold 7/7. **`ref-showcase` is the opposite: a "kitchen
sink" reference** that exercises *every public method* of *every* SDK package, threaded into one
believable domain, seeded with generated sample data, and documented as a per-service tour. It
complements `ref-fastapi` (which stays the lean template seed) and becomes the SDK's living,
browsable documentation.

**Its standing job is to be the coverage/regression canary.** `ref-fastapi` stays the lean seed;
`ref-showcase` is the exhaustive surface that touches every public method first, so an SDK gap or
signature drift breaks *here* before it reaches downstream apps. The cost is that every SDK change
now needs updating in two apps — accepted deliberately, and made enforceable by the coverage gate
below (the `__all__` assertion under *Coverage matrix*) rather than left to diligence.

**Theme: a small "Document Intelligence" service.** Upload documents → chunk & store blobs →
queue-driven pipeline embeds + summarizes via inference → embeddings land in Postgres → semantic
search + per-document chat over the corpus. This one domain naturally forces all five services to
appear in every mode, which a thinner CRUD app can't.

It lives at `examples/ref-showcase/` as a uv workspace member, package `ref_showcase`, its own
canonical `.env` / DB / bucket. It must still **score 7/7** — richer, not sloppier.

**Workspace wiring (follow `ref-fastapi` exactly — and note it needs *zero* root edits):**
`ref-fastapi` has **no** `[tool.uv.sources]` of its own. Its `pyproject.toml` just declares plain
deps on `mini-cloud-config/db/storage/obs[asgi]/inference >=0.1.0`; the `{ workspace = true }`
source mapping that resolves those to editable in-repo packages lives **only in the root
`pyproject.toml`**, and the app is picked up automatically by the root's
`members = ["packages/*", "scaffolder", "examples/*"]` glob. (The `[tool.uv.sources] path =
"../mini-cloud/packages/*"` form is the *sibling generated-app* pattern — a different case that
does **not** apply to an in-repo example.) So `ref-showcase` adds a hatchling `pyproject.toml`
under `examples/` with those same version deps, `[tool.ruff] extend = "../../tooling/ruff-base.toml"`,
and standard pytest/pyright config — and adds **no** `[tool.uv.sources]` block and **no** root
edits. The `examples/*` glob already registers it.

## Coverage matrix — every API surface gets a demonstration

**`config`** — `/debug/config` endpoint renders the typed `Settings` (secrets redacted), shows
which canonical env each service resolved from, and demonstrates `.require()` fail-fast +
`CANONICAL_ENV_KEYS`. Proves "one name per concept, no hardcoding."

**`db` — relational** — `make_pool`, `transaction()` (multi-statement atomic writes for
document+chunks), `acquire`, `connect`, `ConnSource`; **multiple migrations** (`0001_init` …
`0003_*`) to show `migrate`/`discover`/`applied_versions` ordering; real joins (documents ⇄ chunks
⇄ tags), pagination, filtering. Embeddings stored as `float8[]` (no pgvector dependency on the
infra Postgres — cosine computed in-app over the small corpus; a deliberate portability choice).

**`db` — job queue** — dedicated demo endpoints that each provoke one queue feature: normal
enqueue, **priority**, **delayed** (`delay_seconds`), **dedupe** (idempotent re-ingest), a **poison
job** that fails → backoff via `default_backoff` → **dead-letters** (visible via
`dead_letter_count`), a **long job** that heartbeats with `extend()`, and `RetryLater` for
transient-failure retry. `/queue/stats` surfaces `depth()` per queue + dead-letter counts. Three
queues (`ingest`, `embed`, `summarize`) show fan-out.

**Decision — admin "requeue dead-letter" path:** the SDK has `dead_letter_count` and `purge` but
**no requeue-from-dead-letter method**. The showcase surfacing this need is exactly its canary job
(above), so the plan of record is to **add `JobQueue.requeue_dead_letter()` to `mini-cloud-db`**
and consume it here — *not* to reach into the SDK-owned `mini_cloud_dead_letter` table with raw SQL
from the app (that coupling is the anti-pattern a reference app must not model). This makes the
queue tour a small SDK addition + its consumer, sequenced with step 3 below.

**`storage`** — `put_bytes` (text) **and** `put_stream` (multipart file upload); `get_bytes`;
`exists`; `delete`; `list(prefix=, limit=)` with key-prefix namespacing (`docs/`, `chunks/`,
`summaries/`); **`presigned_put_url`** (direct browser→MinIO upload bypassing the app) and
**`presigned_get_url`** (direct download redirect); `ensure_bucket`/`bucket_exists` in `/readyz`.

**`inference`** — `chat` (single-turn summary), `chat_messages` (**multi-turn** chat over a
document), `embed` (query + chunk embeddings feeding semantic search), `models()` listing, and
**streaming** via the `.openai` passthrough (SSE summary endpoint). **Live-required:** the AI
endpoints require a real `MINI_INFERENCE_URL` at runtime — no offline stand-ins. If the gateway is
unset those routes return a clear `503` and `/readyz` reports `inference: false`. **Unit tests mock
the `InferenceClient`** so `make check` still runs fully offline.

**`obs`** — `install()` for HTTP metrics/logging/`/metrics`; **custom business metrics**
(`documents_ingested_total` Counter, `search_latency_seconds` Histogram,
`queue_jobs_processed_total`) to show apps extend the registry; **correlation-ID propagation**
traced request → enqueued job → worker log. `bind_correlation_id` is contextvar-based and
in-process only — it does *not* cross the `enqueue` boundary — so the demo carries the correlation
ID **inside the job payload** and re-binds it in the worker on dequeue; the payload is the carrier,
`bind_correlation_id` is the per-side binder. Plus structured `extra={}` logging
throughout; a **provisioned Grafana dashboard JSON** so the showcase's panels appear in the shared
pane of glass (see *Deliverables* for how it reaches Grafana).

**Coverage gate — "every public method" made enforceable.** The exhaustive-coverage claim is the
whole point, so it can't rest on diligence. A unit test enumerates each SDK package's public
symbols and asserts every one is referenced somewhere in `src/ref_showcase/`. When the SDK grows a
public symbol the showcase doesn't yet exercise, `make check` fails here — the canary role becomes
mechanical rather than aspirational. This also catches the symbols easy to forget:
`config.load_dotenv`/`load_settings`, `queue.purge`, `get/set_correlation_id`/`new_correlation_id`.

Two gotchas the gate spec must nail down before it's built, or it silently under-covers:

- **Scope must include documented submodules, not just top-level `__all__`.** Some public API is
  *not* re-exported at the package root — notably `obs.install`, arguably obs's most important
  symbol, lives in `mini_cloud.obs.asgi.__all__`, not `mini_cloud.obs.__all__`. A gate that walks
  only each package's top-level `__all__` would skip it entirely. So the gate enumerates the
  top-level `__all__` **plus an explicit allowlist of documented public submodules** (`obs.asgi`
  today), and fails if a package grows a new public submodule not in that list — so the "exhaustive"
  canary can't itself miss a whole submodule.
- **Reference ≠ exercise; prefer AST/import over raw grep.** A plain source grep proves a name
  *appears*, not that it's *called* — and symbols like `list`, `exists`, `connect`, `chat`,
  `delete` are common English/method tokens that match comments, docstrings, and unrelated calls,
  giving false "covered." Resolve names via an import-/AST-based check (e.g. confirm each symbol is
  actually imported and referenced as an attribute/call), not a substring scan. This gate reliably
  catches *removed/renamed/added* symbols; it is a weak signal for genuine non-use, and the plan
  treats it as exactly that — a drift canary, not proof of live invocation.

## Sample data

A `make seed` script generates a **deterministic in-repo corpus** (~40–60 short documents built
from a seeded generator — *no network*, so it's reproducible and offline-safe) and populates tags +
relational rows. A `make seed-live` variant runs the full pipeline (upload → ingest → embed →
summarize) through a real gateway. This is what makes the "what does a new project get for free"
story tangible — a populated Grafana dashboard, a searchable corpus, a queue with real throughput.

## Deliverables (file layout)

```
examples/ref-showcase/
├── src/ref_showcase/
│   ├── app.py          # FastAPI: probes + grouped demo routers (config/storage/db/queue/inference/obs tours)
│   ├── resources.py    # SDK wiring (extends ref-fastapi's pattern: +3 queues, custom metrics)
│   ├── domain.py       # document/chunk/tag repository (transaction, joins, pagination)
│   ├── pipeline.py     # ingest → embed → summarize handlers (idempotent)
│   ├── search.py       # embed query + in-app cosine ranking
│   ├── worker.py       # multi-queue worker
│   ├── metrics.py      # custom Prometheus collectors
│   └── seed.py         # deterministic corpus generator
├── migrations/0001..0003_*.sql
├── grafana/dashboard.json   # authored here; provisioned by copying to infra (see below)
├── tests/              # unit (no services, inference mocked) + *_live.py gated by --run-live
├── Makefile  README.md  AGENTS.md  docs/  .env.example  pyproject.toml  pyrightconfig.json
```

Standard Makefile targets (`setup/run/worker/seed/migrate/test/lint/fmt/check/check-live`),
`AGENTS.md`, `docs/` + a **per-service tour doc**, `.env.example` — all the scorecard surface. Add
to the root uv workspace; provision its DB+bucket via `make -C infra project NAME=ref-showcase`.

**Grafana provisioning path (do not skip):** infra loads dashboards from
`infra/config/grafana/dashboards/app-<name>.json` (that's how the existing `app-demo-*.json` panels
appear) — a JSON sitting inside `examples/ref-showcase/grafana/` is **never** picked up on its own.
So the app authors `grafana/dashboard.json` and the provision step copies (or symlinks) it to
`infra/config/grafana/dashboards/app-ref-showcase.json`, matching the `app-<name>.json` convention.
Wire that copy into `make -C infra project NAME=ref-showcase` (or the showcase's own provision/seed
target) so "populated dashboard appears in the shared pane of glass" is actually true.

## Validation

- `make check` (lint + pyright + unit, inference mocked) green with **no services**, including the
  `__all__` coverage-gate test that asserts every public SDK symbol is exercised in `src/`.
- `make check-live` green against ephemeral Postgres (queue variety incl. dead-letter + heartbeat +
  requeue-from-dead-letter exercised).
- `mini score examples/ref-showcase` → **7/7**.
- End-to-end on the live stack (live-gated): `seed-live` → search returns ranked results → Grafana
  shows the custom dashboard populated.
- Per repo convention: run tests **per-package** (`uv run --package <dist> pytest
  examples/ref-showcase`), commit nothing unless asked.

## Rough sequence

1. Scaffold package + pyproject + workspace registration + `.env` + provision DB/bucket.
2. Migrations + `domain.py` (db relational tour) + tests.
3. Add `JobQueue.requeue_dead_letter()` to `mini-cloud-db` (+ its unit test), then
   `resources.py` + multi-queue `pipeline.py`/`worker.py` (queue tour incl. poison/heartbeat/requeue,
   correlation ID carried in the job payload) + tests.
4. Storage tour (streams, presigned, list/delete) + tests.
5. Inference tour (embed/search/multi-turn/stream), live-required with mocked unit tests + tests.
6. `metrics.py` + correlation threading + Grafana dashboard (obs tour), incl. the infra
   `app-ref-showcase.json` copy step in provisioning.
7. `seed.py` corpus + README/AGENTS/docs tour + the `__all__` coverage-gate test; verify 7/7 +
   live e2e (dashboard populated in Grafana).
