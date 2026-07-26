"""The document/chunk/tag repository — the ``db`` *relational* tour.

Everything here talks plain Postgres through the SDK's connection helpers: :func:`transaction`
for atomic multi-statement writes (a document plus its chunks plus its tags land together or not
at all), :func:`acquire` for read paths, real joins across ``documents ⇄ chunks ⇄ tags``, plus
pagination and filtering on the list path. The queue tour lives in ``pipeline.py``; this module is
the relational half.

The repository is bound to a :data:`~mini_cloud.db.ConnSource` (a pool in the app, a single
connection in tests), so a single instance is safe to share across request handlers and the
worker — every method borrows a connection per call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mini_cloud.db import ConnSource, acquire, transaction


@dataclass(frozen=True, slots=True)
class ChunkRow:
    """One chunk of a document, with whether its embedding + blob have been produced yet."""

    id: int
    ordinal: int
    content: str
    chunk_key: str | None
    has_embedding: bool


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """A row for the list view: the document plus aggregates (chunk count, tag names)."""

    id: int
    doc_key: str
    title: str
    source: str
    status: str
    created_at: datetime
    summary_key: str | None
    chunk_count: int
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    """A single document with its ordered chunks and tags (the detail view)."""

    summary: DocumentSummary
    chunks: list[ChunkRow]


class DocumentRepository:
    """CRUD + queries over the document corpus. Plain Postgres, no ORM."""

    def __init__(self, source: ConnSource) -> None:
        self._source = source

    # --- writes ---------------------------------------------------------------------
    def create_document(
        self,
        *,
        doc_key: str,
        title: str,
        source: str,
        chunks: Sequence[str],
        tags: Sequence[str] = (),
    ) -> int:
        """Insert a document, its ordered chunks, and its tag links **atomically**.

        The whole thing runs in one :func:`transaction`, so a unique-key collision on ``doc_key``
        (or any mid-write failure) rolls back the chunks and tag links too — never a half-written
        document. Returns the new document id.
        """
        with transaction(self._source) as conn:
            row = conn.execute(
                "INSERT INTO documents (doc_key, title, source) VALUES (%s, %s, %s) RETURNING id",
                (doc_key, title, source),
            ).fetchone()
            assert row is not None  # RETURNING id always yields a row
            document_id = int(row[0])

            for ordinal, content in enumerate(chunks):
                conn.execute(
                    "INSERT INTO chunks (document_id, ordinal, content) VALUES (%s, %s, %s)",
                    (document_id, ordinal, content),
                )

            for name in tags:
                trow = conn.execute(
                    "INSERT INTO tags (name) VALUES (%s) "
                    "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                    (name,),
                ).fetchone()
                assert trow is not None
                conn.execute(
                    "INSERT INTO document_tags (document_id, tag_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (document_id, int(trow[0])),
                )
        return document_id

    def set_status(self, document_id: int, status: str) -> None:
        with acquire(self._source) as conn:
            conn.execute("UPDATE documents SET status = %s WHERE id = %s", (status, document_id))
            _commit(conn)

    def set_chunk_blob(self, chunk_id: int, chunk_key: str) -> None:
        with acquire(self._source) as conn:
            conn.execute("UPDATE chunks SET chunk_key = %s WHERE id = %s", (chunk_key, chunk_id))
            _commit(conn)

    def set_chunk_embedding(self, chunk_id: int, embedding: Sequence[float]) -> None:
        with acquire(self._source) as conn:
            conn.execute(
                "UPDATE chunks SET embedding = %s WHERE id = %s",
                (list(embedding), chunk_id),
            )
            _commit(conn)

    def set_summary(self, document_id: int, summary_key: str) -> None:
        """Record the summary key and flip status to ``ready`` (the summarize stage is the single
        writer of the terminal status, so the two async stages never race on it)."""
        with acquire(self._source) as conn:
            conn.execute(
                "UPDATE documents SET summary_key = %s, summarized_at = now(), status = 'ready' "
                "WHERE id = %s",
                (summary_key, document_id),
            )
            _commit(conn)

    def delete_document(self, document_id: int) -> bool:
        """Delete a document; chunks and tag links cascade. Returns whether a row was removed."""
        with acquire(self._source) as conn:
            cur = conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))
            _commit(conn)
            return cur.rowcount > 0

    # --- reads ----------------------------------------------------------------------
    def get_document(self, document_id: int) -> DocumentDetail | None:
        """Fetch one document with its aggregates and ordered chunks (two queries, one join for
        tag names). Returns ``None`` if the id is unknown."""
        with acquire(self._source) as conn:
            drow = conn.execute(
                _DOC_SELECT + " WHERE d.id = %s GROUP BY d.id",
                (document_id,),
            ).fetchone()
            if drow is None:
                return None
            crows = conn.execute(
                "SELECT id, ordinal, content, chunk_key, (embedding IS NOT NULL) "
                "FROM chunks WHERE document_id = %s ORDER BY ordinal",
                (document_id,),
            ).fetchall()
        chunks = [
            ChunkRow(
                id=int(r[0]),
                ordinal=int(r[1]),
                content=r[2],
                chunk_key=r[3],
                has_embedding=bool(r[4]),
            )
            for r in crows
        ]
        return DocumentDetail(summary=_row_to_summary(drow), chunks=chunks)

    def list_documents(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        tag: str | None = None,
        status: str | None = None,
    ) -> list[DocumentSummary]:
        """List documents newest-first with chunk-count + tag aggregates, optionally filtered by
        ``tag`` and/or ``status``, paginated by ``limit``/``offset``."""
        where, params = _list_filters(tag, status)
        sql = (
            _DOC_SELECT
            + f" {where} GROUP BY d.id ORDER BY d.created_at DESC, d.id DESC LIMIT %s OFFSET %s"
        )
        with acquire(self._source) as conn:
            # SQL is assembled only from static fragments (_DOC_SELECT + a fixed WHERE shape); every
            # runtime value is a bound parameter, so this is not string-interpolated user input.
            rows = conn.execute(sql, (*params, limit, offset)).fetchall()  # type: ignore[arg-type]
        return [_row_to_summary(r) for r in rows]

    def count_documents(self, *, tag: str | None = None, status: str | None = None) -> int:
        """Total documents matching the same filters as :meth:`list_documents` (for pagination)."""
        where, params = _list_filters(tag, status)
        with acquire(self._source) as conn:
            row = conn.execute(
                f"SELECT count(*) FROM documents d {where}",  # type: ignore[arg-type]  # static fragments; values bound
                tuple(params),
            ).fetchone()
        return int(row[0]) if row else 0

    def chunks_for(self, document_id: int) -> list[ChunkRow]:
        with acquire(self._source) as conn:
            rows = conn.execute(
                "SELECT id, ordinal, content, chunk_key, (embedding IS NOT NULL) "
                "FROM chunks WHERE document_id = %s ORDER BY ordinal",
                (document_id,),
            ).fetchall()
        return [
            ChunkRow(
                id=int(r[0]),
                ordinal=int(r[1]),
                content=r[2],
                chunk_key=r[3],
                has_embedding=bool(r[4]),
            )
            for r in rows
        ]

    def chunks_missing_embedding(self, document_id: int) -> list[ChunkRow]:
        """Chunks of a document that don't yet have an embedding (drives the embed stage)."""
        return [c for c in self.chunks_for(document_id) if not c.has_embedding]

    def iter_embedded_chunks(self) -> list[tuple[int, int, list[float]]]:
        """Every ``(chunk_id, document_id, embedding)`` that has an embedding — the corpus the
        in-app cosine search ranks over (search.py, a later tour). Small-corpus scale by design."""
        with acquire(self._source) as conn:
            rows = conn.execute(
                "SELECT id, document_id, embedding FROM chunks WHERE embedding IS NOT NULL"
            ).fetchall()
        return [(int(r[0]), int(r[1]), [float(x) for x in r[2]]) for r in rows]


# --- shared SQL / row mapping -------------------------------------------------------
# One SELECT list reused by get/list so the column order stays in lock-step with _row_to_summary.
_DOC_SELECT = """
    SELECT d.id, d.doc_key, d.title, d.source, d.status, d.created_at, d.summary_key,
           count(DISTINCT c.id) AS chunk_count,
           coalesce(array_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL), '{}') AS tags
    FROM documents d
    LEFT JOIN chunks c        ON c.document_id = d.id
    LEFT JOIN document_tags dt ON dt.document_id = d.id
    LEFT JOIN tags t          ON t.id = dt.tag_id
"""


def _list_filters(tag: str | None, status: str | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("d.status = %s")
        params.append(status)
    if tag is not None:
        clauses.append(
            "d.id IN (SELECT dt.document_id FROM document_tags dt "
            "JOIN tags t ON t.id = dt.tag_id WHERE t.name = %s)"
        )
        params.append(tag)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _row_to_summary(row: tuple[Any, ...]) -> DocumentSummary:
    return DocumentSummary(
        id=int(row[0]),
        doc_key=row[1],
        title=row[2],
        source=row[3],
        status=row[4],
        created_at=row[5],
        summary_key=row[6],
        chunk_count=int(row[7]),
        tags=list(row[8]) if row[8] else [],
    )


def _commit(conn: Any) -> None:  # noqa: ANN401 — psycopg Connection, kept loose to match SDK style
    """Commit unless the connection is in autocommit (mirrors the SDK's per-call write pattern)."""
    if not conn.autocommit:
        conn.commit()
