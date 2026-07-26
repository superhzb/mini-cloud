-- ref-showcase schema, migration 1 of 3. Applied by mini_cloud.db.migrate() at startup, in
-- lexical order, once each (recorded in mini_cloud_migrations). The queue tables
-- (mini_cloud_jobs, mini_cloud_dead_letter) are created separately by JobQueue.create_schema();
-- these are the APP's own relational tables.
--
-- Document Intelligence core: a document owns ordered chunks. Raw bodies live in object storage
-- (docs/ prefix); only metadata + chunk text live here. Split across three migrations on purpose,
-- so the db tour shows migrate/discover/applied_versions applying ALTERs that build on 0001.

CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,
    doc_key     TEXT        NOT NULL UNIQUE,          -- storage key of the raw upload
    title       TEXT        NOT NULL,
    source      TEXT        NOT NULL DEFAULT 'seed',  -- how it arrived: seed | api | upload
    status      TEXT        NOT NULL DEFAULT 'new',   -- new -> chunked -> ready
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL   PRIMARY KEY,
    document_id BIGINT      NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ordinal     INT         NOT NULL,                 -- 0-based position within the document
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks (document_id);
