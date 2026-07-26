-- Migration 2 of 3: a many-to-many tag taxonomy over documents. Gives the db tour real joins
-- (documents ⇄ document_tags ⇄ tags) and a filterable dimension for the list endpoint.

CREATE TABLE IF NOT EXISTS tags (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT      NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_tags (
    document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    tag_id      BIGINT NOT NULL REFERENCES tags (id)      ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);

CREATE INDEX IF NOT EXISTS document_tags_tag_idx ON document_tags (tag_id);
