"""The seed corpus is deterministic and needs no services to generate."""

from __future__ import annotations

from ref_showcase.seed import CORPUS_SIZE, generate_corpus


def test_corpus_is_deterministic_and_has_expected_shape() -> None:
    first = generate_corpus()
    second = generate_corpus()

    assert first == second
    assert len(first) == CORPUS_SIZE == 48
    assert len({document.title for document in first}) == CORPUS_SIZE
    assert all(document.text and len(document.tags) == 3 for document in first)


def test_a_different_seed_produces_a_different_corpus() -> None:
    assert generate_corpus(seed=1) != generate_corpus(seed=2)
