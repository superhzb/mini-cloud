-- mini-cloud-identity authorization store. Applied by the *service* (not the SDK) on boot against
-- IDENTITY_DATABASE_URL — a database `make -C infra identity-init` provisions but leaves empty. The
-- auth SDK (packages/auth) is a db-less verifier and owns no schema; these tables belong here. See
-- docs/identity-plan.md → "Where per-app grants live".
--
-- Password hashes live in a separate account table; there is still no session/token table. Both
-- username/password and Google OAuth feed the same short-lived platform-JWT mint path.

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

-- Basic username/password login. The default admin/admin row is seeded only when password login
-- is enabled in local development; passwords are salted PBKDF2 hashes, never plaintext.
CREATE TABLE IF NOT EXISTS dev_users (
    username      TEXT        PRIMARY KEY,
    email         TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
