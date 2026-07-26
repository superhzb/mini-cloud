# ref-showcase web console — implementation plan

> Status: **implemented** (2026-07-26).

A human-facing web UI over the `ref-showcase` tour endpoints, so a person can
**generate → examine → verify** real data and SDK behavior from a browser instead of using
`curl` and Swagger `/docs`.

“Console” means a developer/operator workbench, not a terminal and not a polished customer
application. It is still a frontend.

## Outcome

Serve a single-page, zero-build, dependency-free UI from the existing FastAPI process. It must:

- remain useful when optional services are unavailable;
- exercise the document, storage, queue, inference, observability, and analytics tours;
- add no Node runtime, frontend build step, CDN, CORS configuration, or second server;
- preserve the app's 7/7 score and offline `make check`;
- add only one API operation: a bounded, one-click seed endpoint.

## Fixed design decisions

- **Same rare port:** serve the console from the app's existing `PORT` (default `19208`) at
  `/ui/`. Do not introduce a common frontend development port.
- **Static and local:** vanilla HTML, a local CSS file, and a local JavaScript file. No inline
  third-party code and no external assets.
- **Same-origin API calls:** use relative paths for every app request. No CORS.
- **Progressive enhancement:** render useful instructions and links in HTML before JavaScript runs;
  JavaScript owns API interaction and live state.
- **Developer-only mutation:** `/showcase/seed` is an unauthenticated convenience for this local
  reference app. Document that it is not a production administration API.
- **No frontend SDK:** keep a small `api()` wrapper in `console.js` for JSON, error, and
  correlation-header handling.

## Deliverables

```text
src/ref_showcase/
├── app.py
├── seed.py
└── web/
    ├── index.html
    ├── console.css
    └── console.js
tests/
├── test_console_unit.py
└── test_seed_endpoint_unit.py
```

Also update:

- `pyproject.toml` if needed to explicitly include `web/` files in wheel/sdist artifacts;
- `README.md` and `docs/service-tour.md` with a Console section;
- `Makefile` with `ui` as a documented alias for running the same FastAPI server;
- `AGENTS.md` with the new file locations and validation notes.

The `web` directory is app-private data, not a Python public submodule. The SDK surface gate should
not inventory it; update that test only if its public-submodule detector requires an explicit
private-data exclusion.

## Static routing and packaging contract

- Resolve the static directory from `Path(__file__)`, never from the process working directory.
- `GET /ui` returns a `307` redirect to `/ui/`.
- Mount the directory at `/ui` with `StaticFiles(..., html=True)` so:
  - `GET /ui/` returns `index.html`;
  - `GET /ui/console.css` returns CSS;
  - `GET /ui/console.js` returns JavaScript.
- Add `"ui": "/ui/"` to the existing `GET /` discovery response.
- Use relative asset paths in `index.html` so the redirect and mounted path work consistently.
- Verify the three static files exist in both an editable install and a built wheel. Add explicit
  Hatch include configuration if the current package-data defaults do not preserve them.
- The Grafana link is local-infra guidance, not an app API contract: derive it from the current
  browser hostname and the repository's registered Grafana port `3000`. Label it “local Grafana”
  and explain that remote deployments may expose Grafana elsewhere.

## Seed endpoint contract

### API

`POST /showcase/seed?count=N`

- `count` defaults to `6`, minimum `1`, maximum `12`.
- Always use deterministic fallback inference, even when a gateway is configured.
- Seed the first `count` entries of the deterministic corpus. Existing seed titles are skipped, so
  repeating the same request is idempotent.
- Use `app.state.resources`; do not call `build_resources()` and do not open or close a second pool
  inside the request.
- Require DB, queue, and storage. Translate a missing dependency to a clear `503` response.
- Drain only the real pipeline queues (`ingest`, `embed`, `summarize`) synchronously so the result
  is immediately examinable. Never drain the `long` or `poison` demonstration queues.
- If analytics is configured, refresh its deterministic sample event stream too. If it is absent,
  document that in the response without failing document seeding.
- Protect the operation with a process-local non-blocking lock. Return `409` when another seed is
  already running. The reference server runs one Uvicorn process; cross-process locking is out of
  scope and must be noted in the endpoint docstring.
- Return `200` only after the bounded pipeline drain completes.

Response:

```json
{
  "requested": 6,
  "created": 6,
  "skipped": 0,
  "jobs_processed": 18,
  "analytics_events": 96,
  "analytics": "seeded",
  "mode": "offline-fallback"
}
```

`analytics_events` is `0` and `analytics` is `"unavailable"` when analytics is not configured.
Exact event count follows the deterministic analytics generator; the example above is illustrative.

### Required seed refactor

Refactor without changing CLI behavior:

- Extract a resource-accepting operation such as
  `seed_corpus(res, *, count=CORPUS_SIZE, live=False, drain_queues=PIPELINE_QUEUES)`.
- The operation must not close resources it did not create.
- Keep `make seed` and `make seed-live` at the full deterministic corpus size.
- Let the CLI wrapper build and close its own resources.
- Introduce an explicit `PIPELINE_QUEUES` tuple rather than using `WORK_QUEUES` for synchronous
  seeding.
- Preserve stable keys, deterministic ordering, and existing idempotency.

## UI information architecture

Use five navigable sections in one page. On narrow screens they stack; on wider screens the
navigation remains visible and content uses the available width.

### 1. Overview

- Explain the document-intelligence flow and map each section to SDK packages.
- Poll `/healthz`, `/readyz`, and `/queue/stats` at a modest interval while the page is visible.
- Show the readiness matrix for DB, storage, inference, and analytics. Analytics availability may
  be inferred from its endpoint's `503` until `/readyz` reports it explicitly.
- Link to `/docs`, `/metrics`, and the local Grafana URL; show
  `examples/ref-showcase/docs/service-tour.md` as the repository path for the deeper code tour.
- Clearly distinguish “process alive,” “core services ready,” and “optional feature unavailable.”

### 2. Generate

- Create a document through `POST /documents` with title, text, and parsed tags.
- Upload a file through `POST /storage/uploads`.
- State explicitly that a storage upload stores an object only; it does not create a document or
  start the ingestion pipeline.
- Run the bounded one-click sample seed and display its structured result.
- Explain that ordinary document creation is asynchronous and needs `make worker`; the seed button
  drains its own bounded pipeline work synchronously.

### 3. Examine

- Paginated documents table with tag and status filters.
- Document detail drawer showing chunks, tags, status, embedding state, and summary key.
- Storage browser with prefix and limit controls, proxied download, presigned GET/PUT generation,
  and delete with an explicit confirmation.
- Queue depth and dead-letter count.
- Pretty-printed config, DB migration, and observability snapshots.

### 4. Verify

- Semantic search with ranked hits and scores. Explain that search uses deterministic fallback
  embeddings when inference is absent.
- Select a document and submit multi-turn chat.
- Stream a fresh summary token by token. Use `fetch()` plus `ReadableStream` to parse the existing
  SSE response rather than `EventSource`, so the UI can also read status, error bodies, and the
  `X-Correlation-ID` response header.
- List gateway models.
- Disable chat, models, and streamed summary when inference is unavailable; do not disable
  fallback semantic search.

### 5. Analytics

- Show recent events, the four-step funnel, weekly retention cells, and the generated SQL
  reference.
- Provide compact forms for capture, identify, and alias so every analytics endpoint remains
  browser-drivable.
- Keep a stable anonymous `distinct_id` and `session_id` in local storage and send
  `X-Distinct-ID` / `X-Session-ID` on document, search, and chat requests. Let the user reset those
  IDs for a fresh tour.
- Treat analytics as optional and show setup guidance when `MINI_ANALYTICS_DSN` is absent.

## Dependency and degraded-state matrix

| Missing state | Console behavior |
|---|---|
| App DB | Disable documents, queues, search, and seed; keep probes, config, obs, storage, and separately configured analytics available where possible. |
| Storage | Disable document creation, upload, storage browser, and seed; keep DB inspection, queues, and already-embedded search available. |
| Worker not running | Allow document submission, show growing queue depth, and explain that processing is pending; seed remains self-draining. |
| Inference | Mark inference offline; keep seed and fallback search enabled; disable chat, models, and streamed summary. |
| Analytics | Disable analytics mutations/reports and show `MINI_ANALYTICS_DSN` setup guidance; all other sections continue working. |

Every failed request must produce an inline, actionable error containing the HTTP status and API
detail. A `503` is a feature state, not a generic “console broken” toast.

## Cross-cutting browser behavior

- Capture and display the latest `X-Correlation-ID` from every Fetch response.
- Use one request helper that parses JSON errors and handles empty `204` responses.
- Abort superseded list/search requests to prevent stale results from replacing newer results.
- Pause polling when `document.hidden` is true and stop timers/streams on page unload.
- Disable a submitting control until its request finishes; prevent duplicate form submissions.
- Use semantic HTML, associated labels, keyboard-operable controls, visible focus, and an
  `aria-live` status/error region.
- Respect `prefers-color-scheme`; allow a light/dark/system override persisted in local storage.
- Do not render API strings with `innerHTML`.

## Validation and acceptance criteria

### Offline/unit gate

`make check` remains green with no services configured. Tests must prove:

- `/ui` redirects to `/ui/`;
- `/ui/`, `/ui/console.css`, and `/ui/console.js` return `200` with correct content types;
- `GET /` advertises `/ui/`;
- `index.html` references only local static assets and its asset URLs resolve;
- a built wheel contains all three web files;
- seed `count` defaults and bounds are enforced;
- missing DB/queue/storage returns `503`;
- a concurrent seed attempt returns `409`;
- the endpoint uses injected app resources and does not build or close another pool;
- repeated seeding reports created versus skipped documents correctly;
- only ingest/embed/summarize queues are drained;
- analytics present and unavailable response branches are covered.

JavaScript execution does not require adding a browser-test dependency for this zero-build console.
Keep pure transformations small and test API behavior on the Python side; use the live checklist
below for browser integration.

### Live browser checklist

With the local stack running:

1. Open `/ui`, verify assets load with no console errors, and confirm health/readiness polling.
2. Seed six documents twice; the second result reports six skipped and no duplicate documents.
3. Create a document with a worker running and watch its status and queue depth change.
4. Upload, list, download/presign, and delete an object.
5. Search and inspect a ranked result without inference configured.
6. With inference configured, list models, chat over a document, and render a streamed summary.
7. With analytics configured, capture/identify/alias an actor and inspect events, funnel, retention,
   and SQL.
8. Confirm correlation IDs, error states, keyboard navigation, responsive layout, and light/dark
   modes.
9. Run `make check`, `make check-live`, and `mini score examples/ref-showcase`; retain 7/7.

## Implementation order

1. Refactor resource-aware bounded seeding and add its unit tests without changing CLI behavior.
2. Add `POST /showcase/seed` with dependency translation, concurrency guard, and tests.
3. Add and package the three static files; mount `/ui`, update `/`, and test routing/artifacts.
4. Build Overview and Generate, then Examine, Verify, and Analytics against the existing APIs.
5. Add degraded-state handling, accessibility, correlation display, polling cleanup, and theme.
6. Update README, service tour, AGENTS, and Makefile.
7. Complete the offline gate, live browser checklist, live test gate, and 7/7 score verification.
