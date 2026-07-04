"""Shared taxonomy rank-ladder used for taxonomical-similarity scoring.

Both ``Lotus`` (compound source organism) and ``AnnotationOrganism`` (MS2 source
organism) score their similarity to an OTT ``Match`` the same way: find the most
specific taxonomic rank they share. This module is the single definition of that
ladder so the two never drift apart.
"""

# Ranks ordered most-specific first. The similarity score is the position value
# of the most specific *shared* rank: species = len(RANKS) (8), decrementing up
# to domain = 1, and 0.0 when nothing is shared. Every object scored against a
# Match must expose these attribute names.
TAXONOMICAL_RANKS: tuple[str, ...] = (
    "species",
    "genus",
    "family",
    "order",
    "klass",
    "phylum",
    "kingdom",
    "domain",
)

MAXIMAL_TAXONOMICAL_SCORE: float = float(len(TAXONOMICAL_RANKS))  # 8.0


def rank_similarity(organism: object, match: object) -> float:
    """Return the rank-ladder similarity between two taxon-bearing objects.

    ``organism`` and ``match`` each expose the attributes in
    :data:`TAXONOMICAL_RANKS`. The score is ``len(TAXONOMICAL_RANKS)`` for a
    shared species, decrementing up the ladder, and ``0.0`` when no rank is
    shared. A ``None`` on either side never counts as a shared rank (so two
    lineages that are both missing, say, a species do not register a false
    species-level match).
    """
    for offset, rank in enumerate(TAXONOMICAL_RANKS):
        value = getattr(organism, rank)
        if value is not None and value == getattr(match, rank):
            return float(len(TAXONOMICAL_RANKS) - offset)
    return 0.0


def normalized_rank_similarity(organism: object, match: object) -> float:
    """:func:`rank_similarity` normalised to the ``[0, 1]`` range."""
    return rank_similarity(organism, match) / MAXIMAL_TAXONOMICAL_SCORE
