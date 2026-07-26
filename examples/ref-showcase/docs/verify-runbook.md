# ref-showcase — manual verification runbook

A step-by-step guide for a **human** to run the whole stack and confirm every microservice /
SDK tour behaves as intended, ending at the `/ui/` web console. It follows the console plan's
[live browser checklist](console-plan.md#live-browser-checklist) but spells out every command.

> The console is designed to stay useful when optional services are down. So this runbook is
> layered: **Tier A** proves the code offline (no services). **Tier B** brings up core infra and
> proves the full data pipeline. **Tier C** adds the optional inference gateway and analytics DB
> for the AI and product-analytics tours. Stop after whichever tier you have capacity for — each
> tier's checks are self-contained.

All commands run from `examples/ref-showcase/` unless noted. Canonical ports (see
[`docs/env-and-ports.md`](../../../docs/env-and-ports.md)):

| Service | Port | Notes |
|---|---|---|
| ref-showcase API + `/ui/` console | `19208` | this app |
| Postgres (app DB **and** analytics DB) | `5432` | infra |
| MinIO object storage | `9000` (console `9001`) | infra |
| Loki (logs) | `3100` | infra |
| Prometheus | `9090` | infra |
| Grafana | `3000` | infra |
| MLX inference gateway | `19207` | **separate native process**, not in docker-compose |

---

## Prerequisites

- `uv` installed.
- Docker / Colima running (`colima start --cpu 4 --memory 6`) for Tiers B and C.
- The MLX gateway on `:19207` for the Tier C inference tour (optional).

```bash
make setup          # uv sync + copy .env.example -> .env
```

---

## Tier A — offline gate (no services required)

Proves lint, types, and the full unit suite (routing, seed bounds, degraded-state branches) with
nothing running. This is `make check`.

```bash
make check
```

**Expected:** ruff clean, pyright clean, all unit tests pass (~47 passed). This alone verifies:
`/ui` → `/ui/` redirect, the three static assets return `200` with correct content types, `GET /`
advertises `/ui/`, a built wheel contains the web files, seed `count` bounds (`1..12`), the
`503`/`409` seed branches, and that seeding uses injected resources.

Optionally prove the db + queue tours against a throwaway Postgres (needs Docker, no infra stack):

```bash
make check-live     # boots a disposable postgres, runs live db/queue tests, tears it down
```

**Expected:** ~60 passed; container auto-removed on exit.

---

## Tier B — core stack + full data pipeline

Brings up Postgres + MinIO + Loki + Prometheus + Grafana, provisions this app's DB and bucket,
and drives the document → chunk → queue → embed/summarize → search pipeline. No inference gateway
needed (the pipeline uses deterministic offline fallbacks).

### B1. Start infra and provision

```bash
make -C ../../infra up                          # 5 services; wait for healthy
make -C ../../infra ps                           # confirm all healthy
make -C ../../infra project NAME=ref-showcase    # creates the app DB + bucket
```

**Expected:** `ps` shows postgres, minio, loki, prometheus, grafana healthy. `project` prints the
created DB/bucket without a password prompt.

### B2. Run the server and the worker (two shells)

```bash
make run            # shell 1 — API + console on :19208
make worker         # shell 2 — multi-queue background worker
```

### B3. Verify readiness from the API

```bash
curl -s localhost:19208/          | python -m json.tool   # advertises "ui": "/ui/"
curl -s localhost:19208/healthz                            # process alive
curl -s localhost:19208/readyz    | python -m json.tool   # {"ready":true,"checks":{"db":true,"storage":true,...}}
```

**Expected:** `readyz` is `200` with `db` and `storage` true. `inference` appears true only if a
gateway URL is configured (Tier C); its absence does **not** make the app un-ready.

### B4. Verify the console health matrix

Open <http://localhost:19208/ui/>.

- **Overview** — the readiness matrix shows DB and storage green; the browser console (devtools)
  shows no asset errors; health/readiness/queue polling ticks while the tab is visible.
- The Grafana link is derived from the browser hostname + port `3000` and labeled "local Grafana".

### B5. Bounded one-click seed (idempotency)

In **Generate**, click **Seed samples** with count `6`. Then click it a second time.

**Expected:** first run reports `created: 6`, `skipped: 0`, `jobs_processed: 18`; second run reports
`created: 0`, `skipped: 6` (no duplicate documents). `mode` is `offline-fallback`. Equivalent via
curl:

```bash
curl -s -X POST 'localhost:19208/showcase/seed?count=6' | python -m json.tool
curl -s -X POST 'localhost:19208/showcase/seed?count=6' | python -m json.tool   # skipped:6
```

Bounds and concurrency guard:

```bash
curl -s -X POST 'localhost:19208/showcase/seed?count=0'   -o /dev/null -w '%{http_code}\n'  # 422 (min 1)
curl -s -X POST 'localhost:19208/showcase/seed?count=99'  -o /dev/null -w '%{http_code}\n'  # 422 (max 12)
```

### B6. Asynchronous document flow through the worker

In **Generate**, create a document (title/text/tags). Watch **Examine**.

**Expected:** the document first appears with a pending status and the queue depth ticks up, then
the worker processes it (status advances to embedded/summarized, depth falls). Explains the
"needs `make worker`" contract — ordinary creation is async; the seed button is the synchronous path.

Equivalent via curl:

```bash
curl -s -X POST localhost:19208/documents -H 'content-type: application/json' \
  -d '{"title":"Demo","text":"First paragraph.\nSecond paragraph.","tags":["demo"]}'
curl -s localhost:19208/queue/stats | python -m json.tool     # depth per queue + dead_letter count
curl -s 'localhost:19208/documents?tag=demo' | python -m json.tool
```

### B7. Storage tour

In **Examine → Storage browser**: upload a file, list with a prefix, download (proxied), generate a
presigned GET and PUT, then delete (confirm the explicit prompt).

**Expected:** each op succeeds inline; delete returns `204`; a storage upload does **not** create a
document or start ingestion (stated in the UI).

### B8. Fallback semantic search (no inference)

In **Verify → Search**, run a query.

**Expected:** ranked hits with scores, using deterministic fallback embeddings. The UI notes that
search works without inference; chat, models, and streamed summary are **disabled**.

### B9. Debug snapshots + observability

In **Examine**, view the pretty-printed config (secrets redacted), DB migration, and obs snapshots.
Confirm logs are flowing to Loki and metrics to Prometheus/Grafana:

```bash
curl -s localhost:19208/metrics | head             # Prometheus exposition
```

Open Grafana at <http://localhost:13000> and confirm the ref-showcase dashboard renders; query Loki
for the app's logs (each request carries an `X-Correlation-ID`).

---

## Tier C — inference gateway + analytics (optional tours)

### C1. Inference tour

Start the MLX gateway on `:19207` with a chat and an embed model, ensure `.env` has
`MINI_INFERENCE_URL=http://127.0.0.1:19207/v1`, then restart `make run` and `make worker`.

**Expected in the console:** `readyz` now includes `inference: true`; **Verify** enables **Models**,
**Chat**, and **streamed summary**.

- **Models** — lists the gateway's models.
- **Chat** — select a document, submit a multi-turn conversation, get grounded answers.
- **Summary** — stream a fresh summary token-by-token (rendered via `fetch` + `ReadableStream`);
  the `X-Correlation-ID` response header is captured.

Optionally reprocess the corpus through the real gateway:

```bash
make seed-live      # embeds/summarizes via the configured gateway instead of fallbacks
```

### C2. Analytics tour

Provision the **separate** analytics DB and point `.env`'s `MINI_ANALYTICS_DSN` at it (already the
default in `.env.example`):

```bash
make -C ../../infra analytics-init      # creates the `analytics` DB + read-only Grafana role + schema
make seed                                # also backfills the deterministic seed event stream
```

Restart the server. In the console's **Analytics** section:

- Recent events, the 4-step funnel (`document_uploaded → document_processed → search_performed →
  chat_started`), weekly retention cells, and the generated SQL reference all render.
- Use the compact **capture / identify / alias** forms; a stable anonymous `distinct_id` /
  `session_id` is kept in local storage (with a reset control) and sent as `X-Distinct-ID` /
  `X-Session-ID` on document, search, and chat requests.

**Expected without analytics configured:** the Analytics section is disabled and shows
`MINI_ANALYTICS_DSN` setup guidance; every other section keeps working.

---

## Degraded-state spot checks

The console must treat a `503` as a *feature state*, not a broken console. Confirm at least one:

| Take down | Expected console behavior |
|---|---|
| App DB unreachable | documents, queues, search, seed disabled; probes/config/obs/storage still usable |
| Storage down | document creation, upload, browser, seed disabled; DB inspection + already-embedded search still work |
| Worker not running | submission allowed, queue depth grows, "processing pending" shown; seed still self-drains |
| No inference | inference marked offline; seed + fallback search stay enabled; chat/models/summary disabled |
| No analytics | analytics mutations/reports disabled with setup guidance; all else works |

Every failed request should render an inline, actionable error containing the HTTP status and API
detail.

---

## Cross-cutting UI checks (any tier with the console open)

- **Correlation IDs:** the latest `X-Correlation-ID` is surfaced from each fetch response.
- **Stale-result protection:** rapid list/search requests abort superseded ones.
- **Lifecycle:** polling pauses when the tab is hidden and stops on unload; a submitting control is
  disabled until its request finishes (no duplicate submits).
- **Accessibility:** keyboard-operable controls, visible focus, an `aria-live` status/error region;
  API strings are never injected via `innerHTML`.
- **Theme:** respects `prefers-color-scheme` with a persisted light/dark/system override.

---

## Final acceptance gate

```bash
make check                              # offline gate stays green
make check-live                         # ephemeral-Postgres gate stays green
mini score examples/ref-showcase        # from repo root — retain 7/7
```

**Done** when all three pass and every tier you exercised behaved as described above.

---

## Teardown

```bash
# Ctrl-C the run/worker shells
make -C ../../infra down                 # stops infra, keeps volumes/data
```
