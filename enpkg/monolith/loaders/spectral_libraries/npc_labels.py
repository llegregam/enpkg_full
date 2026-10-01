"""Repair of the NPClassifier labels in FragHub exports.

FragHub copies its NPClassifier labels from an ontology table it ships in its own
repository, and the labels in that table were rewritten by two rules, in this order:

1. the substrings ``nan`` and ``none`` were deleted wherever they occur, so ``Lignans``
   reads ``Ligs`` and ``Flavanones`` reads ``Flavas``;
2. a label containing ``", "`` was split there into separate ``|``-separated labels, so
   ``Carotenoids (C40, β-β)`` reads ``Carotenoids (C40|β-β)``.

:class:`NpcLabelRepair` inverts both. It applies the same two rules to every term of the
vendored NPClassifier vocabulary, then replaces a run of neighbouring labels in a cell
that equals a term's rewritten form with the term. A run containing a label that is
itself a vocabulary term is never replaced, and neither is a lone fragment whose partner
is missing, since on its own it names no single term. The evidence and the report sent
upstream are in ``docs/upstream/FRAGHUB_NPCLASSIFIER_LABELS.md``.
"""

from functools import lru_cache
from typing import Final, Iterable, Mapping

from enpkg.monolith.rdf.npc_vocabulary import RANKS, npc_vocabulary

# library_spectra column -> NPClassifier rank of the labels it holds.
RANK_OF_COLUMN: Final[dict[str, str]] = {
    "npc_pathway": "Pathway",
    "npc_superclass": "Superclass",
    "npc_class": "Class",
}

# Separates several labels at one rank within one FragHub cell.
SEPARATOR: Final[str] = "|"


def rewritten_form(term: str) -> tuple[str, ...]:
    """The labels FragHub's ontology table holds in place of ``term``."""
    return tuple(term.replace("nan", "").replace("none", "").split(", "))


class NpcLabelRepair:
    """Restores corrupted NPClassifier labels, one ``|``-separated cell at a time."""

    def __init__(self, terms_by_rank: Mapping[str, Iterable[str]]) -> None:
        """Index the rewritten form of every term, per rank.

        A rewritten form shared by two terms identifies neither, so it is left out and
        cells holding it stay as they are.
        """
        self._genuine: dict[str, frozenset[str]] = {}
        self._originals: dict[str, dict[tuple[str, ...], str]] = {}
        self._longest: dict[str, int] = {}
        for rank, terms in terms_by_rank.items():
            genuine = frozenset(terms)
            sources: dict[tuple[str, ...], set[str]] = {}
            for term in genuine:
                form = rewritten_form(term)
                if form != (term,):
                    sources.setdefault(form, set()).add(term)
            originals = {form: next(iter(found)) for form, found in sources.items() if len(found) == 1}
            self._genuine[rank] = genuine
            self._originals[rank] = originals
            self._longest[rank] = max((len(form) for form in originals), default=1)

    def is_genuine(self, rank: str, label: str) -> bool:
        """Whether ``label`` is a vocabulary term at ``rank``."""
        return label in self._genuine[rank]

    def repair(self, rank: str, cell: str) -> tuple[str, list[tuple[str, str]]]:
        """Return ``cell`` with its corrupted labels restored, and what was replaced.

        The replacements are ``(corrupted text, restored term)`` pairs, the corrupted
        text joined with ``|`` as it appeared in the cell. A cell with nothing to
        restore comes back unchanged with an empty list.
        """
        labels = cell.split(SEPARATOR)
        originals = self._originals[rank]
        genuine = self._genuine[rank]
        restored: list[str] = []
        replacements: list[tuple[str, str]] = []
        i = 0
        while i < len(labels):
            for length in range(min(self._longest[rank], len(labels) - i), 0, -1):
                run = tuple(labels[i : i + length])
                term = originals.get(run)
                if term is None or any(label in genuine for label in run):
                    continue
                restored.append(term)
                replacements.append((SEPARATOR.join(run), term))
                i += length
                break
            else:
                restored.append(labels[i])
                i += 1
        return SEPARATOR.join(restored), replacements


@lru_cache(maxsize=1)
def npc_label_repair() -> NpcLabelRepair:
    """The repair built from the vendored NPClassifier vocabulary, cached per process."""
    vocabulary = npc_vocabulary()
    return NpcLabelRepair({rank: vocabulary.labels(rank) for rank in RANKS})
