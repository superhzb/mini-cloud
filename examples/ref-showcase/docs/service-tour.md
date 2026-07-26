# ref-showcase service tour

This app is intentionally broader than a starter template. It threads each mini-cloud SDK package
through one Document Intelligence flow and keeps unusual surfaces visible as small tour endpoints.
Start at `POST /documents`; follow the same document through the worker, storage, search, metrics,
and logs.

## Web console

`GET /ui` redirects to `/ui/`, where FastAPI serves the packaged, dependency-free developer console
from `src/ref_showcase/web/`. It uses only same-origin relative API requests and remains a single
process on the canonical app port (default `19208`). Overview, Generate, Examine, Verify, and
Analytics expose all of the tours below, including correlation IDs, degraded service states,
responsive navigation, keyboard controls, and a persisted system/light/dark theme.

`POST /showcase/seed?count=N` is the console's only added API operation. The bounded count defaults
to 6 (`1..12`), skips existing deterministic titles, forces offline fallback inference, and drains
only `ingest`, `embed`, and `summarize` before returning structured created/skipped/job/analytics
counts. It reuses `app.state.resources`, does not close the app pool, reports missing DB/queue/
storage as `503`, and returns `409` if its process-local lock is occupied. This unauthenticated
mutation is for the local reference server, not a production admin contract; multi-process locking
is intentionally out of scope.

Code: `app.py`, `seed.py`, `web/`, `tests/test_console_unit.py`,
`tests/test_seed_endpoint_unit.py`.

## Configuration

`GET /debug/config` renders typed `Settings`, the complete `CANONICAL_ENV_KEYS` registry, and a
fail-fast `Settings.require("database_url")` result. Storage credentials and tokens are redacted.
`MINI_INFERENCE_PROJECT` resolves to its own value or `APP_NAME`; the inference SDK turns that into
the gateway's `X-MLX-Project` header.

Code: `sdk_tour.py`, `resources.py`, `.env.example`.

## Relational database and migrations

Three ordered SQL migrations build documents, chunks, tags, pipeline status, summary keys, and
portable `float8[]` embeddings. `DocumentRepository.create_document()` writes a document, all its
chunks, and tag links in one SDK `transaction()`. Reads use `acquire()` and real joins with
pagination and filtering.

`GET /debug/db` compares `discover()` with `applied_versions()` and makes one direct `connect()`
probe alongside the pool path. `GET /documents` and `GET /documents/{id}` are the relational reads.

Code: `migrations/`, `domain.py`, `sdk_tour.py`.

## Postgres job queue

Submitting a document enqueues `ingest`; ingest persists chunk blobs and fans out to `embed` and
`summarize`. The worker round-robins those queues plus `long` heartbeat and `poison` dead-letter
demonstrations. The source and SDK method canary cover priority, delay, dedupe, explicit
retry/backoff, heartbeat extension, dead-lettering, purge, and operator replay through
`requeue_dead_letter()`; live showcase tests drive heartbeat, dead-letter, replay, and correlation.

`GET /queue/stats` reports depth for every worker queue and the dead-letter count.

```text
X-Correlation-ID → ASGI context → submit payload → dequeue → bind_correlation_id → handler logs
```

The context variable is not distributed context; the job payload is the cross-process carrier.

Code: `pipeline.py`, `worker.py`, `resources.py`, `tests/test_queue_tour_live.py`.

## Object storage

The pipeline uses `put_bytes()` for `docs/`, `chunks/`, and `summaries/`. The storage router adds:

- `POST /storage/uploads` — streaming multipart upload with `put_stream()`
- `GET /storage/objects?prefix=docs/` — bounded listing
- `GET /storage/object/content?key=...` — `exists()` plus proxied `get_bytes()`
- `POST /storage/presign` — direct GET or PUT URL
- `DELETE /storage/object?key=...` — object deletion

Startup calls `ensure_bucket()` and readiness checks `bucket_exists()`. Unit tests use a fake
Storage; the MinIO round trip is `--run-live` and canonical-env gated.

## Inference and search

The pipeline calls `embed()` for chunks and `chat()` for summaries when models are configured.
Search embeds the query through the same path, skips dimension-mismatched stored vectors, and
computes cosine in the app over stock Postgres arrays.

- `POST /search` — ranked results; deterministic fallback without a gateway
- `POST /documents/{id}/chat` — grounded multi-turn `chat_messages()`, gateway required
- `GET /inference/models` — advertised gateway models
- `GET /documents/{id}/summary/stream` — SSE through the underlying OpenAI client

`make seed` and offline development remain useful without inference. `make seed-live` requires
`MINI_INFERENCE_URL`, `INFERENCE_MODEL`, and `INFERENCE_EMBED_MODEL`.

## Observability

`mini_cloud.obs.asgi.install()` configures JSON/Loki logging, correlation middleware, standard HTTP
metrics, and `/metrics`. The app extends the shared Prometheus registry in `metrics.py`:

- `documents_ingested_total{source}` — first successful ingest only
- `search_latency_seconds{backend}` — query embed plus in-app ranking
- `queue_jobs_processed_total{queue,outcome}` — dispatch outcome

`GET /debug/obs` shows active correlation and collector metadata. The dashboard is authored at
`grafana/dashboard.json`; `make -C ../../infra project NAME=ref-showcase` copies it to
`infra/config/grafana/dashboards/app-ref-showcase.json`, the directory Grafana mounts. Prometheus
scrapes the app on port `19208`.

## Product analytics

Distinct from observability: `obs` answers *"is the service healthy?"*; analytics answers *"did
**this person** go upload → process → search → chat, and where did they drop off?"* It rides a
**separate** `analytics` Postgres DB (`MINI_ANALYTICS_DSN`, its own migration ledger) and is opt-in
— absent the DSN, the app runs and the tour reports `503`.

The Document Intelligence flow is instrumented as a real 4-step funnel — `document_uploaded`
(`POST /documents`), `document_processed` (worker, on pipeline completion, attributed to the
uploader via a `distinct_id` carried in the job payload), `search_performed` (`POST /search`), and
`chat_started` (`POST /documents/{id}/chat`). The `X-Distinct-Id` / `X-Session-Id` headers bind who
did each action; events are explicit (no auto-capture middleware in v0).

- `POST /analytics/capture` — buffer an arbitrary event (`Analytics.capture`, never blocks)
- `POST /analytics/identify` / `POST /analytics/alias` — the person graph; `alias` stitches an
  anonymous id to an identified one
- `GET /analytics/funnel` — the 4-step funnel with per-step conversion (`run_funnel`)
- `GET /analytics/retention` — weekly retention cohorts (`run_retention`)
- `GET /analytics/events` — the recent append-only stream
- `GET /analytics/sql` — the generated funnel/retention SQL + the package's shipped migrations dir

**Identity is resolved at query time.** `capture()` writes the raw `distinct_id` with `person_id`
NULL — a dumb append that keeps the batched write path off the read path. Funnel/retention SQL
LEFT JOINs `analytics_person_aliases` and collapses anonymous → identified before counting, so a
person who searched anonymously and later logged in counts once.

`make seed` also emits a deterministic, backdated multi-user event stream (40 people, funnel
drop-off, weekly cohorts) so the dashboards have data offline — independent of live traffic and of
the document pipeline. The dashboard `grafana/analytics-dashboard.json` binds to the **Postgres**
datasource (`uid: analytics`, the first Postgres datasource in the stack, read-only) and is copied
by the `project` target's `grafana/*.json` glob. Provision the store with
`make -C ../../infra analytics-init` (DB + read-only Grafana role + schema).

The `postgres` backend is the default; `MINI_ANALYTICS_BACKEND=posthog` is the documented
graduation seam to real PostHog (stubbed in v0) — the platform's "change env, not code" thesis.

Code: `analytics_tour.py`, `resources.py`, `seed.py`, `tests/test_analytics_tour_*`.

## Deterministic corpus and coverage canary

`make seed` generates 48 short documents from a fixed local seed, submits only missing titles, and
drains the three pipeline queues synchronously with inference forced off. The resource-aware
`seed_corpus()` operation never closes caller-owned resources; the CLI wrapper builds and closes its
own. It needs Postgres and MinIO but never the inference gateway. `make seed-live` sends the same
corpus through the real gateway.

`tests/test_sdk_surface_gate.py` imports every top-level SDK `__all__`, inventories each exported
class's own public methods/properties, adds the documented `mini_cloud.obs.asgi` surface, and checks
references through Python AST/import resolution. Comments, docstrings, and unrelated common method
names cannot satisfy it. A newly declared public submodule must be explicitly inventoried. This is
a signature-drift canary, not a substitute for behavior tests.

## Validation modes

```bash
make check
# no services

make check-live
# disposable Postgres; storage and inference deliberately pinned empty

uv run --package ref-showcase pytest examples/ref-showcase --run-live
# repository root + canonical full-stack env for MinIO/gateway tests
```
