# Mini-Cloud Pre-Commit Verification Plan

Developer stories to prove the mini cloud works end-to-end **before the first commit**. The scenario
is the real target: a second Mac mini on the local network, where a developer kicks off a new project
and uses every service for real.

Run these top-to-bottom. Each story has an explicit **acceptance** check; don't commit until Story 11
is green.

## Machines

| Role | Machine | Runs |
|---|---|---|
| **Host** ("mini-cloud host") | Mac mini A | Colima + `infra` compose stack (Postgres, MinIO, Loki, Prometheus, Grafana, Adminer), `brbot-router`, the native MLX inference gateway |
| **Dev** | Mac mini B (the "another Mac") | Only `uv` + a checkout of `mini-cloud`. This is where the developer scaffolds and runs a new app. |

The happy path: a developer sits at **Machine B**, runs ~one command, and gets a live app wired to
every service on **Machine A** — then can observe every service's detail.

Reachability for this plan: **plain LAN IP** (`INFRA_BIND_ADDR` = Machine A's `192.168.x` address).
Simpler than a tailnet but unencrypted on the LAN — fine for a trusted home/office network, not for
anything hostile. Never bind `0.0.0.0`.

---

## Story 0 — Infra off common ports ✅ DONE

**Status: applied and verified in the repo** (2026-07-26). Recorded here so the plan is self-contained.

Host-published ports were moved off well-known defaults so infra never clashes with a developer's own
local Postgres/MinIO/Grafana. Containers still listen on their native ports internally; only the
host-published (left) side of each `docker-compose.yml` mapping changed.

| Service | Host port | Container port |
|---|---|---|
| Postgres | **15432** | 5432 |
| MinIO S3 API | **19000** | 9000 |
| MinIO console | **19001** | 9001 |
| Grafana | **13000** | 3000 |
| Loki | **13100** | 3100 |
| Prometheus | **19090** | 9090 |
| Prometheus pushgateway | **19091** | 9091 |
| Adminer | 18432 | 8080 |
| MLX inference gateway | 19207 (native, not in compose) | — |

The scaffolder now stamps these into every generated `.env`, and **all four service URLs
(`DATABASE_URL`, `STORAGE_ENDPOINT`, `LOKI_URL`, `MINI_INFERENCE_URL`) derive from
`INFRA_BIND_ADDR`** — so a LAN-bound stack scaffolds apps that reach logs and inference on the host,
not their own loopback.

**Acceptance (already met):** `docker compose config` renders the rare published ports; 34/34
scaffolder tests pass; `_env_file` produces host-IP URLs under a LAN bind and identical loopback URLs
under the default.

---

## Story 1 — Host brings the stack up and opens it to the LAN

*As the host operator, I bring up all services on a LAN-reachable address so Machine B can reach them.*

1. On Machine A, in `infra/.env`: set `INFRA_BIND_ADDR` to Machine A's **specific LAN IP** (e.g.
   `192.168.0.42`), never `0.0.0.0`.
2. **Replace the dev credentials** — `POSTGRES_PASSWORD`, `STORAGE_ACCESS_KEY`/`STORAGE_SECRET_KEY`
   (drop `minioadmin`/`*_dev_change_me`), and `GRAFANA_ADMIN_PASSWORD`. Leaving loopback makes these a
   real trust boundary.
3. **Remove or firewall Adminer** before the non-loopback bind — its auto-login plugin exposes the
   Postgres superuser across every project DB.
4. `colima start --cpu 4 --memory 6` → `make -C infra up`.
5. Start `brbot-router` and the native MLX gateway. Confirm the MLX gateway listens on the LAN
   interface, not just `127.0.0.1` (the scaffolder points apps at the host IP, but the gateway must
   actually accept connections there).

**Acceptance:**
- `make -C infra ps` → all six containers healthy, bound to `192.168.0.42:<rare-port>`.
- From **Machine B**:
  - `pg_isready -h 192.168.0.42 -p 15432`
  - `curl http://192.168.0.42:19000/minio/health/live`
  - `curl http://192.168.0.42:13100/ready`
  - `curl http://192.168.0.42:19090/-/healthy`
  - `curl http://192.168.0.42:13000/api/health`
  - `curl http://192.168.0.42:19207/v1/models` (MLX)

  …all succeed.

---

## Story 2 — Developer kicks off a new project from Machine B *(the core happy path)*

*As a developer on the other Mac, I scaffold a working, routed app in one command.*

1. On Machine B, point the scaffolder at Machine A. In the infra `.env` the scaffolder reads (or via
   env), `INFRA_BIND_ADDR=192.168.0.42`, plus the router API vars:
   `MINI_ROUTER_API_URL=http://192.168.0.42:<router-port>`, `MINI_ROUTER_API_TOKEN=<token>`,
   `MINI_ROUTER_API_HOST=<router DASHBOARD_DOMAIN>`.
2. `mini new demo-lan --type fastapi`

**Observe each of the 7 scaffold steps report success:** allocate ports (19101–19299) → render
template → **provision DB + bucket on Machine A** → write canonical `.env` (with `192.168.0.42` URLs)
→ **register the brbot-router route via live `POST /routes`** → drop the per-app Grafana dashboard →
`uv sync` + `git init`.

**Acceptance:**
- The generated `../demo-lan/.env` shows `DATABASE_URL`, `STORAGE_ENDPOINT`, `LOKI_URL`,
  `MINI_INFERENCE_URL` all on `192.168.0.42` (proves the bind fix).
- `mini score ../demo-lan` prints **7/7**.
- `make -C ../demo-lan run` boots; `curl <app>/readyz` → green (it reached its DB + bucket on A).

---

## Stories 3–9 — Observe every service *for real* (from the running `demo-lan` app)

| # | Service | Developer action | Acceptance |
|---|---|---|---|
| 3 | **Postgres (db-per-project)** | `make -C ../demo-lan migrate`; hit an endpoint that writes a row | Row visible via `make -C infra psql` / Adminer on A in the app's *own* db+role; the app role **cannot** see other projects' DBs (least-privilege) |
| 4 | **Job queue** | Enqueue a job via an endpoint; run `make -C ../demo-lan worker` | Job moves pending → done; force a failure → lands in `mini_cloud_dead_letter`; `requeue_dead_letter()` re-runs it |
| 5 | **Object storage (bucket-per-project)** | Upload-file endpoint; list; presigned GET | Object appears in the app's bucket in the MinIO console (`192.168.0.42:19001`); presigned URL downloads; delete works |
| 6 | **Inference (MLX)** | `POST /search` (embed) and a chat endpoint | Real embeddings + chat completion returned; requests carry `X-MLX-Project`; `GET /inference/models` lists models. (If this fails from B but works on A, the gateway is loopback-only — see Story 1 step 5.) |
| 7 | **Logs (Loki)** | Generate traffic; open Grafana Explore | App's structured logs queryable, filtered to `demo-lan`; correlation-id threads request → job |
| 8 | **Metrics (Prometheus)** | `curl <app>/metrics`; open Grafana | Prometheus scrapes the app; the auto-dropped per-app dashboard shows request rate + p95 |
| 9 | **Analytics** | Fire the funnel events; run funnel/retention | Events land in the separate `analytics` DB; the Grafana analytics dashboard renders the funnel; the read-only role can `SELECT` but not `INSERT` |

Grafana for all observability stories: `http://192.168.0.42:13000`.

---

## Story 10 — Public routing / remote upstream

*As a developer, my app on Machine B is reachable through the host's router.*

- `mini route add --domain demo-lan.brettbot.ca --upstream-host <B-hostname-or-IP> --port <app-port>`
  registers a `kind:"remote"` route on A's router.

**Acceptance:** a request to the router for that host proxies to Machine B's app (no spawn on A);
`mini route remove demo-lan` → subsequent request 404s. DNS (`*.brettbot.ca` Cloudflare record) is a
documented **manual** step — note it, don't block the plan on it.

---

## Story 11 — Full gate green, then commit

- Per package (not root `pytest` — sibling `conftest.py`s collide on `--run-live`):
  `uv run --package <dist> pytest packages/<dir>`
- `make -C examples/ref-fastapi check` and `mini score examples/ref-fastapi` → **7/7** (regression guard).
- `make -C infra check-live` (ephemeral throwaway Postgres) → green.
- Re-run the offline stack sanity after the port remap: `make -C infra down && make -C infra up`, then
  the Story 1 health checks.

**Only then commit** — the repo currently has zero commits; everything is untracked.

---

## Known caveats carried into this plan

- **MLX gateway bind.** The gateway is native (not in the compose stack). The scaffolder points apps at
  the host IP, but the gateway process itself must listen on the LAN interface for Story 6 to pass from
  Machine B. Operational step on the host.
- **DNS is not automated.** Creating the `*.brettbot.ca` subdomain record is manual (Story 10).
- **Dev credentials must not survive a non-loopback bind.** Replace them in Story 1; they exist only
  for single-machine loopback dev.
