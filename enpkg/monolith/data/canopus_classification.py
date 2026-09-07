"""Data class for a CANOPUS chemical-class prediction.

One instance represents SIRIUS/CANOPUS's predicted NPClassifier taxonomy for a
single feature (one row of ``canopus_formula_summary.tsv`` or
``canopus_structure_summary.tsv``). Unlike
:class:`~enpkg.monolith.data.sirius_annotation.SiriusChemicalAnnotation`, which is
one of many ranked *structure* candidates per feature, CANOPUS emits exactly one
classification per feature — so a spectrum holds at most one of these.

CANOPUS classifies the *molecular formula*, not a structure, which is what makes it
useful: it still predicts a compound class for features that no database structure
matches. The three NPClassifier ranks (pathway -> superclass -> class, increasingly
specific) each come with the model's own probability, kept as-is rather than
rescaled or thresholded.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class ChemicalTaxonRank:
    """One NPClassifier rank: the predicted label and CANOPUS's confidence in it.

    Attributes:
        label: The NPClassifier term exactly as SIRIUS spells it (e.g.
            ``Iridoids monoterpenoids``). Resolved to an ``npc:`` IRI at
            serialization time, not here — see ``enpkg.monolith.rdf.npc_vocabulary``.
        probability: CANOPUS's confidence in this rank, in ``[0, 1]``. Note that a
            genuine ``0.0`` occurs in real output, so this must never be tested for
            truthiness.
    """

    label: str
    probability: Optional[float]


@dataclass(slots=True)
class CanopusClassification:
    """CANOPUS's NPClassifier prediction for one feature.

    Attributes:
        molecular_formula: Neutral molecular formula CANOPUS classified (e.g. ``C18H24O13``).
        adduct: Assumed adduct in the standard bracket form (e.g. ``[M+H]+``), with
            SIRIUS's internal spaces removed.
        pathway: Broadest NPClassifier rank (7 terms exist), or ``None`` if absent.
        superclass: Intermediate rank, or ``None`` if absent.
        chemical_class: Most specific rank, or ``None`` if absent. Named
            ``chemical_class`` because ``class`` is a Python keyword; it is the
            NPClassifier "class" rank, nothing more.
    """

    molecular_formula: str
    adduct: str
    pathway: Optional[ChemicalTaxonRank] = None
    superclass: Optional[ChemicalTaxonRank] = None
    chemical_class: Optional[ChemicalTaxonRank] = None

    def ranks(self) -> list[tuple[str, ChemicalTaxonRank]]:
        """Return the populated ranks as ``(npc rank name, value)`` pairs.

        The rank name is the NPClassifier/EMI spelling (``Pathway`` / ``Superclass``
        / ``Class``), which is the key the vocabulary lookup needs — the same label
        can exist at two ranks, so the rank is not optional context.
        """
        pairs = (
            ("Pathway", self.pathway),
            ("Superclass", self.superclass),
            ("Class", self.chemical_class),
        )
        return [(name, rank) for name, rank in pairs if rank is not None and rank.label]
