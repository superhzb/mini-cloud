-- Migration 3 of 3: columns the async pipeline fills in AFTER a document is created. Kept out of
-- 0001 deliberately — ordered ALTERs that build on the initial schema are what make the
-- migrate/discover/applied_versions ordering visible in the db tour.
--
-- Embeddings are stored as a plain DOUBLE PRECISION[] (float8[]) rather than pgvector: the infra
-- Postgres is stock postgres:16-alpine with no vector extension, so cosine similarity is computed
-- in-app over the small corpus (search.py). A deliberate portability choice — the wire contract
-- stays plain Postgres.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary_key   TEXT;         -- storage key of summary
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summarized_at TIMESTAMPTZ;

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_key TEXT;                 -- storage key of chunk blob
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding DOUBLE PRECISION[];   -- in-app cosine vector
