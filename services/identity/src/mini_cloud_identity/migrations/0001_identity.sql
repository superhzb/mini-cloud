-- mini-cloud-identity authorization store. Applied by the *service* (not the SDK) on boot against
-- IDENTITY_DATABASE_URL — a database `make -C infra identity-init` provisions but leaves empty. The
-- auth SDK (packages/auth) is a db-less verifier and owns no schema; these tables belong here. See
-- docs/identity-plan.md → "Where per-app grants live".
--
-- This is the WHOLE authentication-adjacent schema: there is no password column and no session /
-- token table. Google is the login authority; access is a short-lived JWT. What we persist is
-- *authorization* (who may enter which app, at what role), not *authentication* state.
--
-- The dev-only `dev_users` table is intentionally NOT here — it is created at boot only when dev
-- login is enabled, so it never ships in a graduated schema (see app.py / devlogin.py).

-- Profile cache, populated from Google's id_token. Convenience only; never a source of authZ.
CREATE TABLE IF NOT EXISTS users (
    sub         TEXT        PRIMARY KEY,        -- Google subject ("google-oauth2|…"); stable per user
    email       TEXT        UNIQUE,             -- the join key to `grants`
    name        TEXT,
    picture     TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The one authorization table: who may use which app, at what role. `app = '*'` is a platform-wide
-- grant (the SDK's WILDCARD_APP) — one row authorizes every app. Keyed by email (not sub) so a
-- grant can be issued before the person has ever logged in.
CREATE TABLE IF NOT EXISTS grants (
    email       TEXT        NOT NULL,
    app         TEXT        NOT NULL,           -- an app name, or '*' for platform-wide
    role        TEXT        NOT NULL,           -- viewer | member | admin | an app's bespoke role
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (email, app)
);

-- Look up all of one person's grants at mint time.
CREATE INDEX IF NOT EXISTS grants_email_idx ON grants (email);
