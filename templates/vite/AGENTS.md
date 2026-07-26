# AGENTS.md — {{name}} (vite)

A Vite + React frontend (`mini new --type vite`). Backend-complete until the Phase-5 TS SDK.

## Bootstrap
```bash
make setup    # npm install + .env
make check    # typecheck + lint + build
```

## Task entrypoints
| Command | Does |
|---|---|
| `make setup` | install deps, seed `.env` |
| `make run` | dev server on :{{web_port}} |
| `make build` | production build |
| `make lint` / `make fmt` | eslint / autofix |
| `make check` | typecheck + lint + build |

## Layout
| Path | Contents |
|---|---|
| `src/main.tsx` | React entry |
| `src/App.tsx` | root component (calls `/api/*` via the dev proxy) |
| `vite.config.ts` | dev server port + `/api` proxy to the backend |
| `.env.example` | `VITE_API_URL` — the one canonical name for the API origin |

## Conventions
- Never hardcode the API host — use `VITE_API_URL` / the `/api` proxy.
- The typed client, config, and observability arrive with `@mini-cloud/sdk` (Phase 5); wire them in
  when available instead of hand-rolling an `apiClient.ts`.
