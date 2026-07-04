"""Unit tests for the shared taxonomy rank-ladder."""

from types import SimpleNamespace

import pytest

from enpkg.monolith.data.taxonomy import (
    MAXIMAL_TAXONOMICAL_SCORE,
    TAXONOMICAL_RANKS,
    normalized_rank_similarity,
    rank_similarity,
)

_FULL = dict(
    species="Artemisia annua",
    genus="Artemisia",
    family="Asteraceae",
    order="Asterales",
    klass="Magnoliopsida",
    phylum="Tracheophyta",
    kingdom="Plantae",
    domain="Eukaryota",
)


def _taxon(**overrides):
    return SimpleNamespace(**{**_FULL, **overrides})


def test_maximal_score_is_ladder_length():
    assert MAXIMAL_TAXONOMICAL_SCORE == float(len(TAXONOMICAL_RANKS)) == 8.0


def test_exact_match_is_maximal():
    assert rank_similarity(_taxon(), _taxon()) == 8.0
    assert normalized_rank_similarity(_taxon(), _taxon()) == 1.0


# Progressive mismatches walk the ladder down one rung at a time.
@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"species": "x"}, 7.0),
        ({"species": "x", "genus": "x"}, 6.0),
        ({"species": "x", "genus": "x", "family": "x"}, 5.0),
        ({"species": "x", "genus": "x", "family": "x", "order": "x"}, 4.0),
        ({"species": "x", "genus": "x", "family": "x", "order": "x", "klass": "x"}, 3.0),
        (
            {"species": "x", "genus": "x", "family": "x", "order": "x", "klass": "x", "phylum": "x"},
            2.0,
        ),
        (
            {k: "x" for k in ("species", "genus", "family", "order", "klass", "phylum", "kingdom")},
            1.0,
        ),
        ({k: "x" for k in _FULL}, 0.0),
    ],
)
def test_ladder_steps(overrides, expected):
    assert rank_similarity(_taxon(**overrides), _taxon()) == expected


def test_normalized_is_scaled():
    assert normalized_rank_similarity(_taxon(species="x"), _taxon()) == 7.0 / 8.0


def test_none_never_counts_as_a_match():
    """Two lineages both missing a rank must NOT register a match there."""
    empty = SimpleNamespace(**{rank: None for rank in TAXONOMICAL_RANKS})
    assert rank_similarity(empty, empty) == 0.0

    # None at species on both sides is skipped; the genus match wins at 7.0.
    a = _taxon(species=None)
    b = _taxon(species=None)
    assert rank_similarity(a, b) == 7.0
