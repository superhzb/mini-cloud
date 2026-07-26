"""Live tests for the db relational tour (domain.py). Postgres only; run with --run-live.

These are the executable spec of the repository contract: atomic multi-statement writes, joins,
pagination + filtering, and cascade delete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

if TYPE_CHECKING:
    from ref_showcase.resources import Resources

pytestmark = pytest.mark.live


def test_create_get_roundtrip(live_resources: Resources) -> None:
    repo = live_resources.require_repo()
    doc_id = repo.create_document(
        doc_key="docs/a.txt",
        title="Alpha",
        source="seed",
        chunks=["one", "two", "three"],
        tags=["ml", "docs"],
    )
    detail = repo.get_document(doc_id)
    assert detail is not None
    assert detail.summary.title == "Alpha"
    assert detail.summary.status == "new"
    assert detail.summary.chunk_count == 3
    assert sorted(detail.summary.tags) == ["docs", "ml"]
    assert [c.ordinal for c in detail.chunks] == [0, 1, 2]
    assert all(not c.has_embedding for c in detail.chunks)
    assert repo.get_document(999999) is None


def test_transaction_rolls_back_on_duplicate_doc_key(live_resources: Resources) -> None:
    repo = live_resources.require_repo()
    repo.create_document(
        doc_key="docs/dup.txt", title="First", source="seed", chunks=["x"], tags=["t"]
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.create_document(
            doc_key="docs/dup.txt",  # collides -> the whole write must roll back
            title="Second",
            source="seed",
            chunks=["y", "z"],
            tags=["t2"],
        )
    # The rolled-back write left no trace: one document, its single chunk, and no stray 't2' tag.
    docs = repo.list_documents()
    assert len(docs) == 1
    assert docs[0].chunk_count == 1
    assert repo.count_documents(tag="t2") == 0


def test_list_pagination_and_tag_filter(live_resources: Resources) -> None:
    repo = live_resources.require_repo()
    for i in range(5):
        repo.create_document(
            doc_key=f"docs/{i}.txt",
            title=f"D{i}",
            source="seed",
            chunks=["c"],
            tags=["even"] if i % 2 == 0 else ["odd"],
        )
    assert repo.count_documents() == 5

    page1 = repo.list_documents(limit=2, offset=0)
    page2 = repo.list_documents(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {d.id for d in page1}.isdisjoint({d.id for d in page2})  # pagination doesn't overlap

    evens = repo.list_documents(tag="even")
    assert len(evens) == 3
    assert all("even" in d.tags for d in evens)
    assert repo.count_documents(tag="odd") == 2
    assert repo.count_documents(status="new") == 5


def test_delete_cascades_and_is_idempotent(live_resources: Resources) -> None:
    repo = live_resources.require_repo()
    doc_id = repo.create_document(
        doc_key="docs/del.txt", title="Del", source="seed", chunks=["a", "b"], tags=["x"]
    )
    assert repo.delete_document(doc_id) is True
    assert repo.get_document(doc_id) is None
    assert repo.chunks_for(doc_id) == []  # cascaded
    assert repo.delete_document(doc_id) is False  # already gone


def test_embedding_roundtrip_stores_float8_array(live_resources: Resources) -> None:
    from ref_showcase.pipeline import fallback_embedding

    repo = live_resources.require_repo()
    doc_id = repo.create_document(
        doc_key="docs/e.txt", title="E", source="seed", chunks=["hello world"]
    )
    chunk = repo.chunks_for(doc_id)[0]
    vec = fallback_embedding("hello world")
    repo.set_chunk_embedding(chunk.id, vec)

    assert repo.chunks_missing_embedding(doc_id) == []
    embedded = repo.iter_embedded_chunks()
    assert len(embedded) == 1
    _chunk_id, got_doc_id, stored = embedded[0]
    assert got_doc_id == doc_id
    assert stored == pytest.approx(vec)  # float8[] round-trips
