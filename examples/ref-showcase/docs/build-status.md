# ref-showcase — build status

Staged against the *Rough sequence* in [`../../../docs/ref-showcase-plan.md`](../../../docs/ref-showcase-plan.md).

## Built

- **Step 1 — scaffold.** Package, `pyproject.toml` (registered via the root `examples/*` workspace
  glob — no root edits), Makefile with the standard targets, `.env(.example)`, `pyrightconfig.json`,
  `check-live.sh`, README/AGENTS. DB + bucket provisioned with
  `make -C ../../infra project NAME=ref-showcase`.
- **Step 2 — db relational tour.** Three ordered migrations (`0001_init` → `0002_tags` →
  `0003_pipeline_columns`) and `domain.py` (`DocumentRepository`: atomic `transaction()` writes,
  joins, pagination + filtering, cascade delete, `float8[]` embeddings). Live tests in
  `tests/test_domain_live.py`.
- **Step 3 — queue tour + SDK addition.** Added **`JobQueue.requeue_dead_letter()`** to
  `mini-cloud-db` (+ live tests in that package). `resources.py` (three fan-out queues + two demo
  queues), `pipeline.py` (ingest → embed + summarize; `long` heartbeat; `poison` dead-letter;
  correlation-in-payload; `dispatch`), `worker.py` (multi-queue). Live tests in
  `tests/test_queue_tour_live.py` and `tests/test_pipeline_live.py`.

Also present from step 3's integration: `app.py` core flow (probes + document
ingest/list/detail + `/queue/stats`) so the app runs and has an offline unit-test surface.

- **Step 4 — storage tour.** Five endpoints over the namespaced bucket: `POST /storage/uploads`
  (`put_stream` multipart), `GET /storage/objects` (`list(prefix=,limit=)`), `GET
  /storage/object/content` (app-proxied `get_bytes` + `exists` 404), `POST /storage/presign`
  (`presigned_get_url`/`presigned_put_url`), `DELETE /storage/object` (`delete`). Offline unit
  tests use a fake Storage (`tests/test_storage_tour_unit.py`); a real-MinIO round-trip lives in
  `tests/test_storage_tour_live.py` (skips when `STORAGE_*` is absent, like the pipeline test).
- **Step 5 — inference tour.** `search.py` (embed query + in-app cosine over the `float8[]`
  vectors, dimension-mismatch guard) and four routes: `POST /search`, `POST
  /documents/{id}/chat` (multi-turn `chat_messages`), `GET /inference/models`, and `GET
  /documents/{id}/summary/stream` (SSE via the `.openai` passthrough). AI routes 503 without a
  gateway; search degrades to the offline fallback like the pipeline. Unit tests mock
  `InferenceClient` (`tests/test_inference_tour_unit.py`, `test_search_unit.py`); live tests hit a
  real gateway (`test_inference_tour_live.py`, skip unless `MINI_INFERENCE_URL` is set).
- **SDK addition (canary caught it).** The live gateway now enforces per-project identification via
  an `X-MLX-Project` header on chat/embed/stream (not on `/models`) — which the `inference` SDK
  didn't send. Fixed at the SDK, not in the app: added canonical **`MINI_INFERENCE_PROJECT`** to
  `mini-cloud-config` and a `project=` default header in `InferenceClient` (defaults to
  `MINI_INFERENCE_PROJECT` → `APP_NAME`). So every app is identified for free; `ref-fastapi` gets
  it too. New SDK unit tests in `packages/inference/tests/test_inference.py`; env registry +
  `.env.example` updated.
- **Step 6 — observability tour.** `metrics.py` adds `documents_ingested_total`,
  `search_latency_seconds`, and `queue_jobs_processed_total` at ingest/search/dispatch boundaries.
  `GET /debug/obs` exposes active correlation and collector metadata. The six-panel dashboard is
  authored at `grafana/dashboard.json`, copied to Grafana's mounted
  `infra/config/grafana/dashboards/app-ref-showcase.json` by the infra `project` target, and backed
  by a Prometheus `ref-showcase` scrape job.
- **Step 7 — corpus, docs, and coverage gate.** `seed.py` deterministically generates 48 local
  documents; `make seed` forces offline fallback inference while `make seed-live` requires the
  configured gateway models. `docs/service-tour.md`, README, and AGENTS map every service.
  `test_sdk_surface_gate.py` resolves imports/references through AST, inventories top-level
  `__all__` plus `mini_cloud.obs.asgi`, detects newly declared public submodules, and explicitly
  canaries `MINI_INFERENCE_PROJECT`.

**Validation:** offline `ruff`+`pyright`+`pytest` green (**31 passed / 14 live-skipped**). Full
live suite vs the real stack + gateway (`local-chat`/`local-embedding`, project header): **45
passed**. Config + inference SDK offline suites green; `ref-fastapi` offline unchanged (6 passed).

> **Known pre-existing issue (not from this work):** the inference SDK's own live test
> `test_chat_round_trip` picks `models()[0]`, which on this multi-model gateway is `local-align`
> (not a chat model) → 400. Independent of the header fix (proven working); it just assumes the
> first advertised model is chat-capable. Left as-is — a robust fix needs a model-selection policy.

## Step 6–7 verification

- Offline `make check`: **39 passed / 14 live-skipped**, Ruff and Pyright green.
- Full package-local live suite with `local-chat` / `local-embedding`: **53 passed**.
- `mini score examples/ref-showcase`: **7/7**.
- Dashboard source/provisioning copies are valid, byte-identical JSON.
- Offline seed e2e: **48 documents created / 144 jobs processed**; semantic search returned ranked
  hits; a second seed run created and processed **0** (idempotent).

No implementation steps remain.
