"""Unit tests for search.py — cosine math and offline (fallback-embedded) ranking, no services.

These prove the ranking logic without a gateway: the corpus is embedded with the same deterministic
fallback the query uses, so the chunk matching the query text ranks first. The gateway path is
exercised (mocked) by the inference-tour endpoint tests.
"""

from __future__ import annotations

import pytest

from ref_showcase.pipeline import fallback_embedding
from ref_showcase.resources import Resources
from ref_showcase.search import cosine, semantic_search


def _settings():
    from mini_cloud.config import load_settings

    return load_settings(dotenv=None)  # process env only, no ./.env coupling


class FakeRepo:
    """Minimal DocumentRepository stand-in: the two reads semantic_search + the endpoint touch."""

    def __init__(self, corpus: dict[int, str]) -> None:
        # corpus: chunk_id (== document_id here) -> text, embedded with the offline fallback.
        self._corpus = corpus

    def iter_embedded_chunks(self) -> list[tuple[int, int, list[float]]]:
        return [(cid, cid, fallback_embedding(text)) for cid, text in self._corpus.items()]


def test_cosine_identical_and_orthogonal() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_magnitude_is_zero() -> None:
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        cosine([1.0, 2.0], [1.0])


def test_semantic_search_ranks_the_matching_chunk_first() -> None:
    corpus = {
        10: "alpha beta gamma",
        11: "zeta eta theta iota",
        12: "alpha beta delta",
    }
    res = Resources(settings=_settings(), repo=FakeRepo(corpus), inference=None)
    hits = semantic_search(res, "alpha beta gamma", limit=3)

    assert hits[0].document_id == 10  # exact-text chunk wins
    assert hits[0].score == pytest.approx(1.0)
    # scores are sorted descending
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_semantic_search_respects_limit() -> None:
    corpus = {i: f"word{i} common" for i in range(10)}
    res = Resources(settings=_settings(), repo=FakeRepo(corpus), inference=None)
    assert len(semantic_search(res, "common", limit=3)) == 3


def test_semantic_search_skips_dimension_mismatched_vectors() -> None:
    class MixedRepo(FakeRepo):
        def iter_embedded_chunks(self) -> list[tuple[int, int, list[float]]]:
            good = super().iter_embedded_chunks()
            good.append((99, 99, [0.1, 0.2, 0.3]))  # wrong-width vector from a different embedder
            return good

    res = Resources(settings=_settings(), repo=MixedRepo({1: "alpha beta"}), inference=None)
    hits = semantic_search(res, "alpha beta", limit=10)
    # The 3-dim stray is skipped, not scored — no crash, and it never appears in the ranking.
    assert {h.document_id for h in hits} == {1}


def test_semantic_search_empty_corpus_returns_empty() -> None:
    res = Resources(settings=_settings(), repo=FakeRepo({}), inference=None)
    assert semantic_search(res, "anything") == []
