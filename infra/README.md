# infra — the mini-cloud infra stack

Postgres with pgvector, MinIO, Loki, Prometheus, Grafana in one docker-compose file. Data lives in
Docker **named volumes** (never in the repo). The *same file* runs on the Mac mini and on a Linux
VPS.

## Prerequisite

A container engine + `docker compose`. **Not currently installed on this machine.** Install one:

```bash
brew install --cask docker           # Docker Desktop, then launch it
# or a lighter CLI-only engine:
brew install colima docker docker-compose && colima start
```

## Run

```bash
cp .env.example .env      # loopback-only defaults are safe as-is; change passwords for non-loopback
make up                   # docker compose up -d
make ps                   # health of every service
make config               # validate/render the effective compose file
```

Endpoints (loopback default):

| Service | URL / port |
|---|---|
| Grafana | http://localhost:13000 (admin / `GRAFANA_ADMIN_PASSWORD`) |
| Adminer (DB browser) | http://localhost:18432 (server `postgres`, `POSTGRES_USER` / `POSTGRES_PASSWORD`) — **dev only** |
| MinIO console | http://localhost:19001 |
| MinIO S3 API | http://localhost:19000 |
| Postgres | `127.0.0.1:15432` (`make psql`) |
| Prometheus | http://localhost:19090 |
| Loki | http://localhost:13100 |

Infra publishes on **rare host ports** (not the well-known 5432/9000/3000/…) so it never clashes
with a developer's own local Postgres/MinIO/Grafana; containers still listen on their native ports.

Grafana boots with **Loki + Prometheus datasources already provisioned** and an
"mini-cloud infra overview" dashboard.

## Browse / edit the database

`make adminer` (or open http://localhost:18432). Log in with server `postgres` and the
`POSTGRES_USER` / `POSTGRES_PASSWORD` from `.env`, then pick any project database from the
dropdown to view tables and fields, inline-edit values, delete rows, or run SQL — handy for
inspecting data changes and deciding whether a field is worth keeping.

> **Dev/loopback only.** Adminer logs in as the Postgres **superuser** and can mutate every
> project DB. It must be removed from the compose file or placed behind real auth **before** any
> non-loopback bind or VPS graduation (stricter than the read-only Grafana panes).

## Create a project's DB + bucket (by hand)

```bash
make project NAME=demo-x
# → creates Postgres role+db 'demo-x', enables pgvector, and creates its MinIO bucket
```

(The scaffolder automates this in Phase 4; this is the manual path.)

`vector` is enabled per database. Re-run `make project NAME=<existing-project>` after upgrading the
stack to enable it in an already-provisioned project database; this preserves all existing data.

`create-project.sh` provisions **over the network** — `psql` against `:15432` and `mc` against
`:19000` — the way you'd talk to a managed Postgres/S3, rather than shelling into the containers.
That means it also runs from **any machine on the LAN** (e.g. a second Mac running only the `mini`
CLI), not just the Docker host. Requirements on whichever machine runs it:

- **`psql`** (from `libpq`) and **`mc`** (MinIO client) on `PATH`. On macOS: `brew install libpq
  minio-mc` — `libpq` is keg-only, so add its bin to `PATH`
  (`export PATH="$(brew --prefix libpq)/bin:$PATH"`).
- A local `infra/.env` carrying the admin `POSTGRES_PASSWORD` + `STORAGE_ACCESS_KEY`/
  `STORAGE_SECRET_KEY`, and a bind/connect address that points at the host running the stack.
  `INFRA_BIND_ADDR` is a *bind* address: `0.0.0.0`/empty maps to `127.0.0.1` automatically. To
  reach a **remote** host, set `INFRA_CONNECT_ADDR=<host LAN IP>` (this overrides the connect
  target for both the script and the `.env` the scaffolder writes).
- Postgres `:15432` and MinIO `:19000` reachable from that machine (i.e. the stack is bound to a
  routable interface, not loopback-only).

## Backups

```bash
make backup     # → backups/<timestamp>/{postgres-all.sql.gz, minio/}
./scripts/restore.sh backups/<timestamp>
```

Schedule `scripts/backup.sh` from cron for regular snapshots.

## Binding & security

Every published port is prefixed with `${INFRA_BIND_ADDR:-127.0.0.1}` — **loopback by default**.
Moving `INFRA_BIND_ADDR` to a specific interface IP (e.g. a tailnet address) widens the trust
boundary:

- Postgres already runs `scram-sha-256` (always on).
- Replace the dev `POSTGRES_PASSWORD` and `STORAGE_*` keys in `.env` with strong secrets **before**
  the non-loopback bind — it's a hard prerequisite, not a follow-up.
- Never bind `0.0.0.0`; bind a specific interface.

See `../docs/MINI_CLOUD_ARCHITECTURE.md` → *Multi-machine development*.

## Supervision (through brbot-router)

The stack runs under `brbot-router` as one **`command`-kind** entry: it appears on the dashboard
with a Start / Stop / Restart control set (wired to `make up`/`make down`/`make restart`) and a
one-click link to Grafana. The router controls the stack's lifecycle but does **not** parent it —
`make up` runs `docker compose up -d`, so the containers detach and survive a router restart, and
the router never idle-reaps or auto-starts the stack on boot. Bring it up once via the dashboard's
Start action or `make up`.

Add the entry to `brbot-router/projects.json` (see `brbot-router-entry.json` in this directory for
the exact block, including the console `links`):

```json
{ "name": "mini-cloud", "kind": "command", "path": "../mini-cloud/infra", "command": "true",
  "commands": { "start": "make up", "stop": "make down", "restart": "make restart" },
  "healthPort": 13000, "alwaysOn": true, "siteUrl": "http://<router-host>.local:13000" }
```

## Pinned images

PostgreSQL 16 / pgvector 0.8.5 · MinIO 2024-09-13 · Loki 3.1.1 · Prometheus 2.54.1 · Grafana
11.2.0. Bump one at a time — a version pin per service keeps a Postgres bump from touching MinIO
(decoupling model).
