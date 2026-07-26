-- mini-cloud-analytics event store. Applied by the consumer against MINI_ANALYTICS_DSN (a
-- *separate* database from the app's own DATABASE_URL) via mini_cloud.db.migrate(). This is the
-- first SDK package that ships and applies its own migrations — see docs/analytics-plan.md.
--
-- Design: the event stream is append-only and "dumb" — capture() writes the raw distinct_id and
-- leaves person_id NULL. Anonymous->identified resolution is deferred to query time, where funnel /
-- retention SQL joins events through analytics_person_aliases. That keeps the batched write path a
-- pure append with no per-event read.

CREATE TABLE IF NOT EXISTS analytics_events (
    id             BIGSERIAL   PRIMARY KEY,
    event          TEXT        NOT NULL,
    distinct_id    TEXT        NOT NULL,
    person_id      TEXT,                              -- nullable: resolved at query time (see above)
    project        TEXT        NOT NULL,
    session_id     TEXT,
    properties     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    timestamp      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- event time (client may backdate)
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- server ingest time
    correlation_id TEXT
);

-- Funnel/segmentation hot path: filter by project+event over a time window.
CREATE INDEX IF NOT EXISTS analytics_events_project_event_ts_idx
    ON analytics_events (project, event, timestamp);

-- Per-person timelines (funnel first-touch, retention cohorts).
CREATE INDEX IF NOT EXISTS analytics_events_distinct_ts_idx
    ON analytics_events (distinct_id, timestamp);

-- One row per identified person; a person accumulates many distinct_ids over time.
CREATE TABLE IF NOT EXISTS analytics_persons (
    person_id     TEXT        PRIMARY KEY,            -- the identified distinct_id
    distinct_ids  TEXT[]      NOT NULL DEFAULT '{}',
    properties    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Anonymous -> identified map. previous_id (the anonymous id) resolves to distinct_id (the person).
-- Query-time identity resolution LEFT JOINs events on previous_id and coalesces to distinct_id.
CREATE TABLE IF NOT EXISTS analytics_person_aliases (
    previous_id  TEXT        PRIMARY KEY,
    distinct_id  TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS analytics_person_aliases_distinct_idx
    ON analytics_person_aliases (distinct_id);
