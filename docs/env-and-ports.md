# Canonical env-var names & port registry

The single source of truth for what shared services are called and where they listen. Ends the
three-URL inference split (`8933` / `9000` / `5900`) and the divergent env names
(`MLX_GATEWAY_URL` vs `MLX_PLATFORM_BASE_URL`, `HF_TOKEN` vs `HUGGING_FACE_HUB_TOKEN`,
`API_PORT` vs `PORT` vs `--port`).

**Rule:** one name per concept, one value per name. New apps use these names verbatim. The `config`
SDK package (Phase 2) loads exactly these keys.

## Canonical environment variables

| Env var | Meaning | Example (loopback default) |
|---|---|---|
| `MINI_INFERENCE_URL` | OpenAI-compatible inference gateway base URL (the one canonical name — replaces `MLX_GATEWAY_URL` / `MLX_PLATFORM_BASE_URL`) | `http://127.0.0.1:19207/v1` |
| `MINI_INFERENCE_PROJECT` | Identifies the calling project to the multi-tenant gateway (sent as the `X-MLX-Project` header). Optional — defaults to `APP_NAME`. | `<app>` |
| `DATABASE_URL` | Postgres connection string for this app's database | `postgresql://<app>:<pw>@127.0.0.1:15432/<app>` |
| `STORAGE_ENDPOINT` | S3/MinIO endpoint URL | `http://127.0.0.1:19000` |
| `STORAGE_ACCESS_KEY` | S3 access key id | `minioadmin` (dev only) |
| `STORAGE_SECRET_KEY` | S3 secret access key | `minioadmin` (dev only) |
| `STORAGE_BUCKET` | This app's bucket | `<app>` |
| `STORAGE_REGION` | S3 region (MinIO ignores; keep for portability) | `us-east-1` |
| `LOKI_URL` | Loki push endpoint for structured logs | `http://127.0.0.1:13100` |
| `PROMETHEUS_PUSHGATEWAY_URL` | Optional metrics push target (scrape is preferred) | `http://127.0.0.1:19091` |
| `MINI_ANALYTICS_DSN` | Postgres DSN to the shared `analytics` product-event store (`mini_cloud.analytics`). Distinct from `DATABASE_URL` — a separate DB on the same `:15432`. | `postgresql://analytics_ro:...@127.0.0.1:15432/analytics` |
| `MINI_ANALYTICS_BACKEND` | `postgres` \| `posthog` — event sink. `posthog` is the documented graduation seam. | `postgres` |
| `MINI_ANALYTICS_PROJECT` | Tags analytics events with the calling project. Optional — defaults to `APP_NAME`. | `<app>` |
| `HF_TOKEN` | Hugging Face token (canonical — replaces `HUGGING_FACE_HUB_TOKEN`) | — |
| `PORT` | The port this app's HTTP server binds (canonical — replaces `API_PORT`, `--port`) | per port registry |
| `LOG_LEVEL` | `debug` \| `info` \| `warn` \| `error` | `info` |
| `APP_ENV` | `dev` \| `staging` \| `prod` (replaces prod/staging directory copies) | `dev` |
| `INFRA_BIND_ADDR` | Interface the infra stack binds (infra host only). A *bind* address — `0.0.0.0`/empty is treated as `127.0.0.1` when used as a connect target. | `127.0.0.1` |
| `INFRA_CONNECT_ADDR` | Overrides the host that `create-project.sh` / the scaffolder *connect to* for provisioning + the emitted `DATABASE_URL`/`STORAGE_ENDPOINT`. Set to the infra host's LAN IP when running `mini new` from another machine. Defaults to `INFRA_BIND_ADDR` (with the `0.0.0.0`→loopback mapping). | `192.168.0.12` |
| `ROUTE_REGISTRATION_TOKEN` | **brbot-router** bearer token that enables the `POST /routes` registration API. Unset ⇒ API disabled (default). | — |
| `MINI_ROUTER_API_URL` | Scaffolder → router API base URL (client side). Defaults to `http://127.0.0.1:<router PORT>`. | `http://machine-a.local:9000` |
| `MINI_ROUTER_API_TOKEN` | Scaffolder's copy of `ROUTE_REGISTRATION_TOKEN`. When unset (and none in the router's `.env`), `mini new` writes `projects.json` directly. | — |
| `MINI_ROUTER_API_HOST` | `Host` header the scaffolder sends to the route API (the router's `DASHBOARD_DOMAIN`, since the API is scoped to that host). | `dashboard.brettbot.ca` |

Deprecated names and their canonical replacement (migrate on next touch):

| Old | Canonical |
|---|---|
| `MLX_GATEWAY_URL`, `MLX_PLATFORM_BASE_URL` | `MINI_INFERENCE_URL` |
| `HUGGING_FACE_HUB_TOKEN` | `HF_TOKEN` |
| `API_PORT`, `--port`, bespoke `PORT` variants | `PORT` |
| `STORAGE_ROOT`, `HUB_API_ARTIFACTS` (filesystem-as-store) | `STORAGE_ENDPOINT` + `STORAGE_BUCKET` |

## Port registry

Ranges keep app dev servers, app APIs, and infra from colliding. `brbot-router` lazy-spawns apps on
their assigned ports. Infra services publish on **rare host ports** (not their well-known defaults) so
they never clash with a developer's own local Postgres/MinIO/Grafana; each container still listens on
its native port internally — only the host-published port is remapped in `docker-compose.yml`.

### Infra stack (always-on, loopback by default)

| Service | Host port | Container port | Notes |
|---|---|---|---|
| Postgres | `15432` | 5432 | one instance, database-per-project |
| MinIO API (S3) | `19000` | 9000 | bucket-per-project |
| MinIO console | `19001` | 9001 | web UI |
| Grafana | `13000` | 3000 | one pane of glass; router `siteUrl` deep-links here |
| Loki | `13100` | 3100 | log ingest + query |
| Prometheus | `19090` | 9090 | metrics scrape + query |
| Prometheus pushgateway | `19091` | 9091 | optional; only if an app can't be scraped |
| Adminer (DB browser) | `18432` | 8080 | **dev/loopback only** — Postgres data browser (view/edit/delete rows across all project DBs); superuser access, remove or gate before any non-loopback bind |

### Platform services

| Service | Port | Notes |
|---|---|---|
| `brbot-router` dashboard | (router-owned) | control plane |
| `mlx-platform` gateway | `19207` | native, Apple-GPU; `MINI_INFERENCE_URL` points here |

### Application ranges (assigned by the scaffolder)

| Range | Purpose | In use |
|---|---|---|
| `19101–19199` | app **web** dev servers | 19101 local-tube, 19102 fr-tiktok, 19103 par-ici, 19104 fr-hub, 19105/06 srt-flow |
| `19201–19299` | app **API** servers | 19204 fr-hub-api, 19205 srt-flow prod, 19206 srt-flow stg, 19207 **mlx-platform (reserved)**, 19208 ref-showcase (in-repo example) |

`mini new` picks the next free web/API pair from these ranges and records it in
`brbot-router/projects.json`. **19207 is reserved for the MLX gateway** — do not assign it to an app.

## Notes on binding & security

- The infra stack binds `INFRA_BIND_ADDR` (default `127.0.0.1`). Loopback trust ends the moment
  this moves off loopback: Postgres must switch to `scram-sha-256` and MinIO to real access keys
  (see the architecture doc, *Multi-machine development*). Bind a **specific** interface IP, never
  `0.0.0.0`.
- Dev credentials (`minioadmin`, a dev Postgres password) are for loopback only and must not survive
  a non-loopback bind or a VPS graduation.

## Route registration API (`POST /routes`, Phase 4.5)

`brbot-router` owns `projects.json`. The scaffolder registers routes through this API when the
router is running (approach A) so it never edits the file out from under the router's live state;
it falls back to a direct file write only when the router is **down**. The API is **disabled unless
`ROUTE_REGISTRATION_TOKEN` is set**, and is served on the **dashboard host** (so it can't shadow an
app's own `/routes` path).

```
POST   /routes        Authorization: Bearer <ROUTE_REGISTRATION_TOKEN>   Host: <DASHBOARD_DOMAIN>
DELETE /routes/<name> Authorization: Bearer <ROUTE_REGISTRATION_TOKEN>   Host: <DASHBOARD_DOMAIN>
```

`POST /routes` is **idempotent on `name`** (re-registering updates in place) and persists the entry
to `projects.json`. Responses: `201` created · `200` updated/removed · `400` invalid body · `401`
bad token · `404` unknown route (DELETE) · `503` API disabled.

Body — two route kinds:

| Field | `kind:"app"` (local, lazy-spawn) | `kind:"remote"` (proxy to another machine) |
|---|---|---|
| `name`, `domain`, `port` | required | required (`port` = upstream port) |
| `path`, `command` | required (repo path + spawn command) | ignored (defaults to placeholders) |
| `upstreamHost` | n/a | host to proxy to, e.g. `machine-b.local` (default `127.0.0.1`) |
| `siteUrl` | optional dashboard deep-link | optional |

A `remote` route is proxied but never spawned, readiness-probed, idle-reaped, or git-managed; the
router reports it as **running** (the proxy 502s if the far end is down). Example:

```json
{ "name": "demo-b", "kind": "remote", "domain": "demo-b.brettbot.ca",
  "upstreamHost": "machine-b.local", "port": 19250, "siteUrl": "https://demo-b.brettbot.ca" }
```

**DNS is not automated** — creating the `*.brettbot.ca` Cloudflare record for a new subdomain is a
separate step.
