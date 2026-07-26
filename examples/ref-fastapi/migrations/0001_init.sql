-- ref-fastapi schema. Applied by mini_cloud.db.migrate() at startup, in order, once each.
-- The queue tables (mini_cloud_jobs, mini_cloud_dead_letter) are created separately by the SDK's
-- JobQueue.create_schema(); this file is for the APP's own tables.

-- A minimal audit of notes accepted, so the app owns some relational state of its own (the point
-- of the db package). The note bodies live in object storage; only metadata lives here.
CREATE TABLE IF NOT EXISTS notes (
    id          BIGSERIAL PRIMARY KEY,
    note_key    TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
