# mini-cloud-identity

The platform's **login authority** (Phase 6). It bounces a human through Google OAuth, reads their
per-app authorization from a `grants` table, and mints a short-lived, **asymmetrically-signed**
platform JWT. Apps never talk to this service on the hot path — they link the tiny
[`mini-cloud-auth`](../../packages/auth) SDK, fetch this service's *public* keys from
`/.well-known/jwks.json`, and verify tokens locally.

One token, one fixed `aud: "mini-cloud"`, platform-wide. *Which* app a human may use, at what role,
is the `grants` claim (`{app: role}`) — never `aud`. See [`docs/identity-plan.md`](../../docs/identity-plan.md).

## Endpoints

| Route | Purpose |
|---|---|
| `GET /login` | Redirect to Google (Authorization Code + PKCE, stateless signed `state`). |
| `GET /callback` | Verify Google's `id_token`, fold in grants, mint the platform JWT. |
| `GET /.well-known/jwks.json` | Public signing keys — the trust anchor every verifier reads. |
| `GET /userinfo` | Decode the caller's own bearer token → `{sub, email, grants}`. |
| `POST /dev/token` | **Dev-only** password grant → the *same* JWT (see below). |
| `GET /healthz` · `GET /readyz` | Liveness / readiness (store reachable, key + dev-login state). |

## Run it locally

```bash
make -C infra identity-init                 # provision the empty `identity` DB + owner role
export IDENTITY_DATABASE_URL=postgresql://identity:identity@127.0.0.1:15432/identity
uv run --package mini-cloud-identity mini-cloud-identity      # serves on :19210
```

With **zero setup** (no DB, no key) it still boots: an ephemeral ES256 key and an in-memory grants
store, so a fresh clone can mint a token immediately. Both are logged loudly as ephemeral — mount
`MINI_AUTH_SIGNING_KEY[_FILE]` and set `IDENTITY_DATABASE_URL` for anything that must survive a
restart.

## Dev login (get a JWT with no browser)

Google needs a browser, a real account, and client secrets — too heavy for tests, CI, and `curl`. So
in **local dev** the service also exposes `POST /dev/token`, which mints the **same** platform JWT
after a username/password check. A default `admin` / `admin` account is seeded with a platform-wide
(`"*"`) admin grant, so it authorizes on every app out of the box.

```bash
curl -s localhost:19210/dev/token -d '{"username":"admin","password":"admin"}' \
     -H 'content-type: application/json' | jq -r .access_token
# → drop into  Authorization: Bearer <token>  /  MINI_AUTH_TEST_TOKEN
```

**It fails closed.** `MINI_AUTH_DEV_LOGIN` defaults to `1`, but the service **refuses to boot** if
it's enabled while the deployment doesn't look like local dev (a non-loopback `MINI_AUTH_ISSUER` or
`APP_ENV != dev`). Forgetting to set `MINI_AUTH_DEV_LOGIN=0` on graduation is a startup crash with a
clear message, not a silent `admin/admin` backdoor. Add more testers with rows in `dev_users` +
`grants` (or `MINI_AUTH_DEV_ADMIN_USER`/`_PASSWORD` to change the default admin).

## Configuration

App verifiers read only `MINI_AUTH_ISSUER` / `MINI_AUTH_JWKS_URL` / `MINI_AUTH_AUDIENCE`. Everything
below is **service-side only** (secrets live here, never in an app):

| Env var | Meaning | Default |
|---|---|---|
| `MINI_AUTH_ISSUER` | This service's public URL; the JWT `iss` | `http://127.0.0.1:19210` |
| `MINI_AUTH_AUDIENCE` | Fixed platform `aud` | `mini-cloud` |
| `IDENTITY_DATABASE_URL` | Private DSN to the `identity` store | *(in-memory if unset)* |
| `MINI_AUTH_SIGNING_KEY` / `_FILE` | ES256/RS256 private key PEM (inline or mounted path) | *(ephemeral if unset)* |
| `MINI_AUTH_KID` / `MINI_AUTH_ALGORITHM` | Signing key id / algorithm | `mini-auth-1` / `ES256` |
| `MINI_AUTH_ACCESS_TTL` | Access-token lifetime (seconds) | `900` |
| `GOOGLE_CLIENT_ID` / `_SECRET` / `GOOGLE_REDIRECT_URI` | Google OAuth client | *(login 503s if unset)* |
| `MINI_AUTH_DEV_LOGIN` | Enable `POST /dev/token` + seed the admin | `1` (local dev) |
| `MINI_AUTH_DEV_LOGIN_FORCE` | Override the fail-closed guard (deliberate) | `0` |
| `MINI_AUTH_DEV_ADMIN_USER` / `_PASSWORD` | Seeded admin credentials | `admin` / `admin` |

## Tests

```bash
uv run --package mini-cloud-identity pytest services/identity
```

Fully offline (in-memory store, ephemeral key) — the dev grant exercises the exact mint → sign →
verify path Google feeds. The browser leg is manual/live.
