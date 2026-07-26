-- {{name}} schema. Applied by mini_cloud.db.migrate() in order, once each.
-- The queue tables are created separately by JobQueue.create_schema(); this is for YOUR tables.

CREATE TABLE IF NOT EXISTS notes (
    id          BIGSERIAL PRIMARY KEY,
    note_key    TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
