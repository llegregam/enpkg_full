"""Data class for a SIRIUS structure-identification annotation.

One instance represents a single ranked candidate structure that SIRIUS proposed
for a feature (one row of ``structure_identifications_top-X.tsv``). Like
:class:`~enpkg.monolith.data.chemical_annotation.AnnotationOrganism` it is a
lightweight ``slots`` dataclass — a full SIRIUS run yields thousands of these, so
it holds only what the knowledge graph needs: the per-feature rank, the candidate's
molecular formula, the assumed adduct, and the 2D (skeleton) InChIKey.
"""

from dataclasses import dataclass


@dataclass(slots=True)
class SiriusChemicalAnnotation:
    """A single ranked SIRIUS candidate structure for a feature.

    Attributes:
        rank: SIRIUS ``structurePerIdRank`` — the candidate's rank for its feature
            (1 = best). Ranking is SIRIUS's own; it is preserved, not recomputed.
        molecular_formula: Neutral molecular formula of the candidate (e.g. ``C6H9N3O3S``).
        adduct: Assumed adduct in the standard bracket form (e.g. ``[M+K]+``), with
            SIRIUS's internal spaces removed.
        inchikey_2d: 14-character (2D / skeleton) InChIKey of the candidate structure.
    """

    rank: int
    molecular_formula: str
    adduct: str
    inchikey_2d: str
