# Plan: `mini-cloud-identity` — thin platform identity (Google OAuth → JWT)

Phase 6, promoted from *deferred* to *building now*. The reasoning: every app demo needs basic
auth, and we don't want each one reinventing login — a Supabase-style plug-and-play seam owned by
the platform. This plan is scoped by three decisions already taken (see *Scope* below) and mirrors
the shape of [`analytics-plan.md`](analytics-plan.md).

## Scope (decided)

1. **Thin IdP, not full.** Google OAuth → JWT minting only. No self-hosted password auth, no
   session store. Google is the login authority; we mint a short-lived platform JWT after Google
   verifies the human. Follows the existing `srt-flow/pkg-auth` pattern. **One deliberate,
   local-first exception:** a username/password grant for quick testing (see *Quick-test login*) that
   mints the *same* JWT and is **on by default in local dev** (with a seeded default admin) — flipped
   off on graduation, so it's never a login path for real users in production.
2. **Platform-wide identity.** One shared account/JWT system for the whole mini-cloud, with
   **per-app authorization claims** carried in the token — not a separate auth stack per app.
3. **Gateway trust deferred.** Ship the SDK helper + identity service + a reference-app proof
   (pieces 1–3). Do **not** touch `mlx-platform` to retire `X-MLX-Project`-as-auth yet — that's the
   riskiest external-repo change and waits until this is proven end-to-end in-repo.

## Positioning — identity vs. the existing auth-ish seams

| Concern | Owner | Answers |
|---|---|---|
| *Is this request from a known human?* | **identity (new)** | authN — a platform JWT with `sub`/`email` |
| *May this human use **this app**, at what role?* | **identity (new)** | authZ — per-app claims in the JWT |
| *Which project is calling the inference gateway?* | `MINI_INFERENCE_PROJECT` → `X-MLX-Project` | service-to-gateway identity (unchanged for now) |
| *Is the service healthy?* | `obs` | aggregated, no identity |

Identity is **authN + coarse authZ for end-users**. `X-MLX-Project` stays as service→gateway
identification; collapsing it into the JWT is the deferred Phase-6b gateway-trust step.

## Architecture decisions (all forks now resolved — see history in git)

1. **Asymmetric JWTs (RS256 or ES256), verified via JWKS.** The identity service holds the private
   signing key; every verifier (SDK in each app, later the gateway) fetches public keys from
   `/.well-known/jwks.json` and validates signature + `iss`/`aud`/`exp` locally. No verifier ever
   needs a shared secret or a call back to identity on the hot path. ES256 preferred (small keys,
   fast); support key rotation via `kid`.
2. **Short-lived access token, no server-side session store** (honors "thin"). Access JWT lives
   ~15–60 min. When it expires the app bounces the user back through Google (Google holds the real
   session, so it's a silent redirect if still signed in). A **signed, stateless refresh token**
   (also verifiable via JWKS, longer TTL) is an optional add if silent re-auth proves too chatty —
   still no session table.
3. **Per-app claims live in the token.** The JWT carries a `grants` claim, e.g.
   `"grants": { "ref-showcase": "admin", "demo-x": "member" }`, plus `sub`, `email`, `iss`, `aud`,
   `iat`, `exp`. The SDK's `require_user(app=..., role=...)` reads `grants[app]`, **falling back to a
   `"*"` wildcard app** (`grants.get(app) or grants.get("*")`) so a platform-wide admin — e.g. the
   dev default admin (Piece 2b) — authorizes everywhere without a row per app. Platform-wide
   identity, per-app authorization — exactly the decision.
4. **One fixed platform `aud`, authZ carried by `grants` (not `aud`).** Because identity is
   platform-wide, the service mints a **single** token that must verify at *every* app — so it
   cannot set `aud` to any one app name. It stamps a constant **`aud: "mini-cloud"`**, and every
   verifier expects that same fixed value. `aud` therefore proves only "a mini-cloud identity token";
   **all per-app authorization is the `grants[app]` check**, never `aud`. (Rejected: `aud=APP_NAME`,
   which is incompatible with one platform-wide token; and `aud`-as-array, which pushes authZ into a
   claim `grants` already owns.)
5. **Identity is its own process, in *this* repo** (`services/identity/`) — not a separate repo. It
   stays a platform L2 service reached only over a wire contract (the same JWKS/JWT boundary it would
   have if external) and registers its route through the Phase-4.5 `POST /routes` API like any app —
   but for local single-machine development a separate repo buys nothing and costs an extra clone
   plus a dependency to keep in sync. It lives in the workspace alongside the `packages/auth` SDK it
   pairs with; because the coupling is only the wire contract, it can graduate to its own repo/VPS
   later exactly like any scaffolded app. (Rejected: a separate `mini-cloud-identity` repo — that
   only mirrored `mlx-platform`'s production-service layout, which has no local benefit.)
6. **Scorecard stays 7/7.** Auth is opt-in per demo — showcased on a reference app and documented
   in the adoption guide, not added as an 8th gate.

## The build

### Piece 1 — SDK `mini-cloud-auth` (`packages/auth/`, namespace `mini_cloud.auth`)

The only Phase-6 code in *this* repo. Small, dependency-light:

- `verify_token(token) -> Principal` — JWKS-caching verifier (fetch + cache public keys by `kid`,
  refresh on unknown `kid`), validates signature, `iss`, `aud`, `exp`, `nbf`. Returns a typed
  `Principal(sub, email, grants: dict[str, str])`.
- `require_user(*, app: str | None = None, role: str | None = None)` — a **FastAPI dependency**:
  extracts the bearer token, verifies it, and enforces `grants[app]` (401 no/invalid token, 403
  wrong app/role). This is the plug-and-play line an app author writes.
- `from_settings()` — builds the verifier from config (below).
- Deps: `mini-cloud-config` + `PyJWT[crypto]` (RS256/ES256 + `PyJWKClient`) + `httpx` for JWKS.
  No dependency on `db`/`storage` — a verifier must stay tiny so any app can adopt it cheaply.
- Tests: verify against a **locally-minted test JWT** (test keypair in the fixture) — fully offline,
  no live identity service needed for unit tests; a `--run-live` test hits the real JWKS.

### Piece 2 — identity service (`services/identity/`, in-repo)

Small FastAPI (or node) service; endpoints:

- `GET /login` → redirect to Google OAuth (PKCE).
- `GET /callback` → verify Google's `id_token`, resolve the user's **grants** (see open fork),
  mint the platform JWT (RS256/ES256, `kid`), hand it back (redirect with fragment/cookie, or JSON
  for API clients).
- `GET /.well-known/jwks.json` → public keys (the trust anchor every verifier reads).
- `GET /userinfo` → decode-and-return current principal (convenience).
- `POST /refresh` → optional, only if decision 2's refresh token is adopted.
- Config: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, signing key material (mounted, **not** in the
  repo), `MINI_AUTH_ISSUER` (its own public URL). Registers via `POST /routes` as
  `identity.brettbot.ca`.

### Piece 2b — quick-test login (dev-only username/password → JWT)

Google OAuth needs a browser, a real Google account, and client secrets — too heavy for unit tests,
CI, `curl`, and fast local iteration. So the service also exposes a **dev-only password grant** that
mints the *same* platform JWT. The point is to exercise the identical verify path with a token
obtained in one HTTP call; only the pre-mint human-check differs.

- **On by default in local dev; disabled on graduation.** `MINI_AUTH_DEV_LOGIN` defaults to **`1`**
  (enabled) — local-first, so a fresh clone can log in and grab a JWT with zero setup, matching the
  platform's "dev-default creds on a trusted LAN" stance. Set `MINI_AUTH_DEV_LOGIN=0` on any
  graduated/VPS deployment; with it off the endpoint returns `404`/`503` and real users can only come
  through Google. (This is the inverse of the router's off-by-default token — the trade favors local
  convenience because the whole box is a single-user trusted machine.)
- **A default admin account, seeded automatically.** When dev login is on, `identity-init` seeds an
  `admin` user (`admin` / password `admin` by default; email `admin@local`) with a
  **platform-wide** grant so it authorizes on *every* app out of the box — a single `("admin@local",
  "*", "admin")` row using a `"*"` wildcard app. This needs a one-line extension to the SDK grant
  check (decision 3): `require_user(app=X)` reads `grants.get(X)` **then falls back to
  `grants.get("*")`**, so a platform admin needn't be granted per app. Override the defaults via
  `MINI_AUTH_DEV_ADMIN_USER` / `MINI_AUTH_DEV_ADMIN_PASSWORD` if desired.
- **Endpoint** `POST /dev/token` — body `{ "username": "...", "password": "..." }` →
  `{ "access_token": "<JWT>", "token_type": "bearer", "expires_in": <sec> }`. Same signing key,
  `kid`, `iss`, `aud: "mini-cloud"`, and TTL as the Google path — **indistinguishable to every
  verifier**, so apps stay single-path (the only SDK touch is the `"*"` fallback above).
- **Dev user store, kept separate from the thin model.** A small `dev_users(username, email,
  password_hash)` table (hashes via argon2/bcrypt — never plaintext), created **only when dev login
  is enabled** so it never ships in a graduated schema and doesn't touch the passwordless `users`/
  `grants` design. Each dev user maps to an **email**; per-app grants still come from the same
  `grants` table (the seeded admin uses the `"*"` wildcard). Add more testers with
  `mini grant`/direct rows as needed.
- **Same mint path.** After the password check the service reads `grants WHERE email = ?`, folds the
  rows into the `grants` claim, and signs — byte-for-byte the `/callback` mint minus the Google step.
- **Convenience for tests:** a `mini token --user tester` CLI (or a documented `curl` one-liner)
  returns a JWT to drop into `Authorization: Bearer …`. This is exactly what populates
  `MINI_AUTH_TEST_TOKEN` for ref-showcase's `--run-live` auth tests today.

### Piece 3 — reference-app proof (`examples/ref-showcase`)

Use `ref-showcase` — the existing SDK-tour canary — so auth joins the other SDKs it already exercises
(rather than standing up a separate `ref-fastapi` proof).

- Add one protected endpoint guarded by `require_user(app="ref-showcase", role="member")`.
- Offline test: mint a test JWT with the fixture keypair, assert 200 with a valid token / 401
  without / 403 with the wrong app-grant.
- Live test (`--run-live`): real Google login → real JWT → real protected call end-to-end.
- Adoption guide: a "wire auth in 3 lines" section (link SDK, add the dependency, set 3 env vars).

### Config + registry additions

Add to `mini-cloud-config` + `docs/env-and-ports.md` + `.env.example`:

| Env var | Meaning | Example |
|---|---|---|
| `MINI_AUTH_ISSUER` | Identity service base URL; the JWT `iss` | `https://identity.brettbot.ca` |
| `MINI_AUTH_JWKS_URL` | JWKS endpoint. Optional — defaults to `${MINI_AUTH_ISSUER}/.well-known/jwks.json` | — |
| `MINI_AUTH_AUDIENCE` | Expected `aud` — the fixed platform audience. Optional — defaults to `mini-cloud`. **Not** per-app (per-app authZ is the `grants[app]` check; see decision 4) | `mini-cloud` |

Service-side only (the identity service, not app verifiers):

| Env var | Meaning | Example |
|---|---|---|
| `MINI_AUTH_DEV_LOGIN` | Enables the `POST /dev/token` password grant + seeds `dev_users` and the default admin. **On by default (`1`)** in local dev; set `0` on a graduated deployment (see *Quick-test login*) | `1` |
| `MINI_AUTH_DEV_ADMIN_USER` | Default admin username seeded when dev login is on. Optional — defaults to `admin` (email `admin@local`, platform-wide `"*"` admin grant) | `admin` |
| `MINI_AUTH_DEV_ADMIN_PASSWORD` | Default admin password. Optional — defaults to `admin` | `admin` |

Proposed **port `19210`** for the identity service — a free slot in the `19201–19299` API band,
just past `mlx-platform` (`19207`). (`env-and-ports.md` groups platform services in a table but
defines no separate numeric band; `19207` itself sits inside `19201–19299`.) Confirm against the
registry and add the row to `env-and-ports.md` when we assign it.

## Where per-app grants live — decided: minimal `identity` DB

"No user/session store" (thin) and "per-app authorization claims" (platform-wide authZ) pull in
opposite directions: the claims must come from *somewhere* at mint time. **Decision: a minimal
provisioned `identity` Postgres database** (like `analytics`) — not a static file.

- **One `grants` table** — `(email, app, role)`, unique on `(email, app)` — plus an optional
  `users` profile cache populated from Google's `id_token` (`sub`, `email`, `name`, `picture`).
  This is the whole schema; there is no password column and no session/token table.
- Still **thin** in the decided sense: **no passwords, no server-side sessions** — Google remains
  the login authority, and access is short-lived JWT. The DB holds *authorization* (who may enter
  which app, at what role), not *authentication* state.
- **Provisioning & schema ownership:** infra owns the Postgres instance, so `make -C infra
  identity-init` creates the `identity` DB **and a writer role for the service** — but stops there.
  The **schema/migrations belong to the identity service** (`services/identity/`; decision 5 — it's
  the sole writer), which applies them on boot, exactly like any scaffolded app owns its own
  migrations. This differs from `analytics`, whose migrations ship inside the in-repo
  `packages/analytics` **SDK package**: here the auth SDK (`packages/auth`) is a tiny db-less
  verifier that owns **no** schema, so "SDK-owned migrations" would be wrong — the *service*, not the
  SDK, owns them. (Now that the service lives in-repo its migration SQL is reachable from this
  workspace, so folding schema-apply into `identity-init` later is an option; we keep the boot-time
  apply for now to match the standard app pattern.) The `grants` table is the seam a tiny admin UI
  (or `mini grant <email> <app> <role>` CLI) can sit on later.
- **Mint path:** on `/callback`, after Google verifies the human, the service reads
  `grants WHERE email = ?`, folds the rows into the `grants` claim (`{app: role}`), upserts the
  `users` cache, and signs the JWT. No grant rows → a valid-identity token with an **empty** grants
  claim (authenticated but unauthorized for every app — `require_user(app=…)` then 403s).

The token contract and SDK are storage-agnostic, so this choice is invisible to app authors — they
only ever see the JWT and `require_user`.

## Sequencing

1. `packages/auth` SDK + offline tests (test keypair) — self-contained.
2. `services/identity/` in-repo service: `make -C infra identity-init` (DB + writer role), the
   service owns/applies its schema on boot, then Google OAuth + JWKS + mint reading the `grants` table.
   Add the dev-only `POST /dev/token` password grant + `dev_users` seed behind `MINI_AUTH_DEV_LOGIN`
   in the same step, so tests/CI can obtain a JWT without a browser from day one.
3. Wire the reference app + live end-to-end proof; adoption-guide section.
4. **(deferred / Phase 6b)** coordinate `mlx-platform` to trust the JWT and retire
   `X-MLX-Project`-as-auth. Out of scope here.

*Done when (this plan):* a reference app authenticates a real Google user against
`mini-cloud-identity`, the SDK verifies the JWT and enforces per-app grants, and the adoption guide
documents the 3-line switch-on — **without** any change to `mlx-platform`.
