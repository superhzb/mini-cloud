# AGENTS.md — ref-showcase

Machine-readable map for agents working in this app (scorecard metric #5).

## What this is

The mini-cloud "kitchen sink" reference: a Document Intelligence service that exercises every
public SDK method. It is the **coverage/regression canary** — an SDK change should break here
first. Keep it at **7/7** on the scorecard; richer, not sloppier.

## Where things live

| Path | Purpose |
|---|---|
| `src/ref_showcase/app.py` | FastAPI app: probes + document and per-service tour endpoints |
| `src/ref_showcase/resources.py` | SDK wiring → one `Resources`; queue names; `build_resources()` |
| `src/ref_showcase/domain.py` | `DocumentRepository` — the db **relational** tour (transaction, joins, pagination) |
| `src/ref_showcase/pipeline.py` | queue **handlers** (ingest/embed/summarize/long/poison), chunking, offline fallbacks, `dispatch` |
| `src/ref_showcase/search.py` | query embedding + portable in-app cosine ranking |
| `src/ref_showcase/metrics.py` | custom Prometheus business collectors |
| `src/ref_showcase/sdk_tour.py` | redacted config, migration, and obs debug helpers |
| `src/ref_showcase/analytics_tour.py` | analytics funnel vocabulary, `track`/query helpers, SDK method canary |
| `src/ref_showcase/seed.py` | deterministic corpus + backdated analytics event stream + offline/live drain |
| `src/ref_showcase/web/` | packaged zero-build console (`index.html`, local CSS, local JavaScript) |
| `src/ref_showcase/worker.py` | multi-queue background worker (round-robins `WORK_QUEUES`) |
| `migrations/0001..0003_*.sql` | ordered schema (documents → tags → pipeline columns) |
| `grafana/dashboard.json` | authored obs dashboard; provisioning copies it into `infra/config/...` |
| `grafana/analytics-dashboard.json` | analytics dashboard (Postgres datasource `uid: analytics`) |
| `docs/service-tour.md` | endpoint/code/test walkthrough by SDK service |
| `tests/test_unit.py` | offline unit tests (no services) |
| `tests/test_*_live.py` | live tests, `--run-live`-gated (`domain`, `queue_tour`, full `pipeline`) |
| `tests/test_sdk_surface_gate.py` | AST/import-resolved `__all__` + `obs.asgi` canary |
| `tests/test_console_unit.py` | `/ui` static routing, local assets, and wheel package-data checks |
| `tests/test_seed_endpoint_unit.py` | bounded resource-aware seed + HTTP lock/degraded-state contract |

## Conventions

- **Config only through canonical env** (`mini_cloud.config`) — never hardcode URLs/ports. Canonical
  names in `.env.example`. App-specific extras (`INFERENCE_MODEL`, `INFERENCE_EMBED_MODEL`) are the
  app's own env, read in `resources.py`.
- **Everything through the SDK** — persistence via `mini_cloud.db`, blobs via `mini_cloud.storage`,
  logs/metrics via `mini_cloud.obs`. No bespoke stores or clients.
- **Handlers are idempotent** (at-least-once delivery). Re-running overwrites the same keys and
  skips already-embedded chunks.
- **Correlation crosses the queue via the payload** — `submit_document` stamps `correlation_id`
  into the job; `pipeline.dispatch` re-binds it on dequeue. `bind_correlation_id` is in-process only.
- **Seed modes are intentional** — `make seed` forces fallback inference; `make seed-live`
  requires URL + chat/embed models.
- **Console seeding reuses app resources** — `/showcase/seed` never builds/closes a second pool,
  forces fallback inference, and drains only `PIPELINE_QUEUES` (never `long`/`poison`). Its lock is
  process-local because the documented server is one Uvicorn process.
- **Console assets are package data** — resolve `web/` from `Path(__file__)`, keep asset requests
  relative, and verify all three files remain in the built wheel. No Node/CDN/CORS layer.
- **Dashboard provisioning has two copies** — author the app-local JSON, then keep the copy under
  `infra/config/grafana/dashboards/` identical. The infra `project` target copies every
  `grafana/*.json` (`dashboard.json` → `app-<name>.json`, others → `app-<name>-<base>.json`).
- **Analytics is opt-in and on a SEPARATE DB** — `MINI_ANALYTICS_DSN` ≠ `DATABASE_URL` (its own
  migration ledger; both start at `0001`, so they must not share one DB). `track()` no-ops when
  analytics is unconfigured. Events are explicit (`X-Distinct-Id`/`X-Session-Id`); identity is
  resolved at query time (funnel/retention join `analytics_person_aliases`), never on the write path.
- **Coverage uses AST/import resolution** — do not replace it with grep. Scope includes the
  explicit `mini_cloud.obs.asgi` allowlist, the `MINI_INFERENCE_PROJECT` canary, and every
  `mini_cloud.analytics` public symbol/method (referenced via `analytics_tour.ANALYTICS_METHOD_CANARY`).

## How to validate

```bash
make check        # lint + pyright src + unit tests — must pass with NO services running
make check-live   # ephemeral throwaway Postgres — db + queue tours (incl. dead-letter + requeue)
make ui           # same FastAPI server as `make run`; browse the canonical port at /ui/
```

Run tests per-package from the repo root: `uv run --package ref-showcase pytest examples/ref-showcase`.
Live tests need `--run-live` and canonical env; the full-pipeline test additionally needs MinIO
(`STORAGE_*`) and skips without it. Commit nothing unless asked.
