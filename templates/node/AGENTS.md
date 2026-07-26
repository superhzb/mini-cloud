# AGENTS.md — {{name}} (node)

A minimal Node HTTP service (`mini new --type node`).

## Bootstrap
```bash
make setup    # npm install + .env
make check    # lint + test
```

## Task entrypoints
| Command | Does |
|---|---|
| `make setup` | install deps, seed `.env` |
| `make run` / `make start` | server on :{{api_port}} |
| `make test` | node --test |
| `make lint` | syntax check |
| `make check` | lint + test |

## Layout
| Path | Contents |
|---|---|
| `src/index.js` | http server; `/healthz`, `/readyz`, `/metrics`, `/` |
| `.env.example` | canonical env (`PORT`, `APP_NAME`) |

## Conventions
- Read `PORT` / `APP_NAME` from env — never hardcode.
- Richer DB/storage/obs come with the Phase-5 TS SDK.
