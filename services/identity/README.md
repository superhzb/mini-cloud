# mini-cloud-identity

The platform's **login authority** (Phase 6). It offers two login methods—basic username/password
for local development and Google OAuth for browser users—then reads per-app authorization from a
`grants` table and mints the same short-lived, **asymmetrically-signed** platform JWT. Apps never
talk to this service on the hot path: they link the tiny
[`mini-cloud-auth`](../../packages/auth) SDK, fetch this service's *public* keys from
`/.well-known/jwks.json`, and verify tokens locally.

One token, one fixed `aud: "mini-cloud"`, platform-wide. *Which* app a human may use, at what role,
is the `grants` claim (`{app: role}`) — never `aud`. See [`docs/identity-plan.md`](../../docs/identity-plan.md).

## Endpoints

| Route | Purpose |
|---|---|
| `GET /login` | Redirect to Google (Authorization Code + PKCE, stateless signed `state`). |
| `GET /callback` | Verify Google's `id_token`, fold in grants, mint the platform JWT. |
| `POST /login/password` | Local-network username/password login → the same platform JWT. |
| `GET /.well-known/jwks.json` | Public signing keys — the trust anchor every verifier reads. |
| `GET /userinfo` | Decode the caller's own bearer token → `{sub, email, grants}`. |
| `POST /dev/token` | Compatibility alias for `POST /login/password`. |
| `GET /healthz` · `GET /readyz` | Liveness / readiness (store reachable, key + dev-login state). |

## Run it locally

```bash
make -C infra identity-init                 # provision the `identity` DB + owner role
cp services/identity/.env.example services/identity/.env
make -C services/identity run               # loads .env and serves on :19210
```

With **zero setup** (no DB, no key) it still boots: an ephemeral ES256 key and an in-memory grants
store, so a fresh clone can mint a token immediately. Both are logged loudly as ephemeral — mount
`MINI_AUTH_SIGNING_KEY[_FILE]` and set `IDENTITY_DATABASE_URL` for anything that must survive a
restart.

## Username/password login

Google needs a browser, a real account, and client secrets — too heavy for tests, CI, and `curl`. So
in **local dev** the service exposes `POST /login/password`, which mints the **same** platform JWT
after a username/password check. A default `admin` / `admin` account is seeded with a platform-wide
(`"*"`) admin grant, so it authorizes on every app out of the box.

```bash
curl -s http://<identity-host>.local:19210/login/password \
     -d '{"username":"admin","password":"admin"}' \
     -H 'content-type: application/json' | jq -r .access_token
# → drop into  Authorization: Bearer <token>  /  MINI_AUTH_TEST_TOKEN
```

**It fails closed in two places.** The service refuses to boot with password login enabled when
`APP_ENV != dev`, and the endpoint accepts only loopback, private-IP, or `.local` Host headers. A
request through the public `identity.brettbot.ca` router gets `403`, even though Google OAuth remains
public. Add testers with rows in `dev_users` + `grants`, or override the seeded developer account
with `MINI_AUTH_ADMIN_USER` / `MINI_AUTH_ADMIN_PASSWORD`. Set `MINI_AUTH_PASSWORD_LOGIN=0` when the
developer account is no longer needed.

## Google OAuth

The implementation follows `srt-flow/pkg-auth`: Authorization Code flow, PKCE, signed state,
Google-JWKS verification, client-audience/issuer/expiry checks, and a required verified email. Put
the Google client ID/secret in the gitignored `.env`, and add the exact callback URI to that OAuth
client's **Authorized redirect URIs** in Google Cloud Console. For this host it is:

```text
https://identity.brettbot.ca/callback
```

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
| `MINI_AUTH_PASSWORD_LOGIN` | Enable `POST /login/password` + seed the admin | `1` (local dev) |
| `MINI_AUTH_DEV_LOGIN_FORCE` | Override the fail-closed guard (deliberate) | `0` |
| `MINI_AUTH_ADMIN_USER` / `_PASSWORD` | Seeded developer-super-admin credentials | `admin` / `admin` |

The former `MINI_AUTH_DEV_LOGIN` and `MINI_AUTH_DEV_ADMIN_*` names remain accepted as compatibility
aliases.

## Tests

```bash
uv run --package mini-cloud-identity pytest services/identity
```

Fully offline (in-memory store, ephemeral key) — the dev grant exercises the exact mint → sign →
verify path Google feeds. The browser leg is manual/live.
