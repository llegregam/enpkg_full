"""NPClassifier term resolution against the vendored Earth Metabolome Ontology.

CANOPUS reports its NPClassifier prediction as a human-readable label (``Terpenoids``,
``Iridoids monoterpenoids``). The knowledge graph wants an IRI in EMI's ``npc:``
namespace, so that features classified alike join on the same node and so that
``skos:broader`` roll-up works. This module is that mapping.

**Why a lookup table and not a string transform.** EMI mints its ``npc:`` locals by
percent-encoding the upper-cased label, so ``Phenolic acids (C6-C1)`` becomes
``PHENOLIC_ACIDS_%28C6-C1%29``. Applying that rule blindly is wrong for two of the 770
terms: ``Miscellaneous alkaloids`` and ``Miscellaneous polyketides`` each exist at *two*
ranks, and EMI disambiguates the Class reading with a ``_CLASS`` suffix. Keying the table
on ``(rank, label)`` is what keeps a Class from being filed under its own Superclass — a
mis-classification that would then double-count under ``skos:broader+``. The minting rule
survives only as the fallback for labels EMI has not vendored yet.

**Version skew.** SIRIUS ships a newer NPClassifier than EMI vendored, so a handful of
labels (``Purine nucleosides``, ``RiPPs``, ...) resolve to no vendored term. Minting those
forward is deliberate: EMI mints by the same rule, so the IRIs match once EMI refreshes,
with no migration. Each is logged once at WARNING, which is what keeps the drift visible
instead of letting the data quietly diverge from the vocabulary.
"""

from __future__ import annotations

import logging
import urllib.parse
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, SKOS

from .namespaces import NPC

_LOGGER = logging.getLogger(__name__)

# Same repo-root resolution the serializer uses for enpkg.ttl: this module sits three
# directories below the root (rdf -> monolith -> enpkg -> root).
_EMI_VOCAB_PATH = Path(__file__).resolve().parents[3] / "docs" / "vocab" / "EMI-vocab.owl"

# The three NPClassifier ranks, in EMI's spelling. Also the valid first element of a
# lookup key -- a label alone is not a key, see the module docstring.
RANKS = ("Pathway", "Superclass", "Class")


def _mint(label: str) -> URIRef:
    """Mint an ``npc:`` IRI the way EMI does: percent-encoded upper-cased label.

    Verified to reproduce 768 of EMI's 770 vendored locals exactly; the two it misses are
    the cross-rank collisions the lookup table handles first.
    """
    return NPC[urllib.parse.quote(label.upper().replace(" ", "_"), safe="_-")]


class NpcVocabulary:
    """The vendored NPClassifier terms, indexed for label -> IRI resolution.

    Construction parses ``docs/vocab/EMI-vocab.owl`` (~1.5 s), so callers should go
    through :func:`npc_vocabulary`, which caches a single instance per process. Building
    one per serializer would cost that parse once per analysis.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        graph = Graph()
        graph.parse(path or _EMI_VOCAB_PATH)
        # (rank, casefolded label) -> IRI. Casefolded because SIRIUS's capitalisation is
        # not guaranteed to track EMI's, and the labels are otherwise identical.
        self._by_label: dict[tuple[str, str], URIRef] = {}
        # IRI -> (rank class, label, broader parents). Everything needed to inline a term.
        # Parents are a *tuple*, not a single value: NPClassifier's taxonomy is a DAG,
        # not a tree — 23 of the 770 terms have two parents (MEROTERPENOIDS sits under
        # both POLYKETIDES and TERPENOIDS). Keeping only one would silently drop
        # taxonomy edges, under-counting every skos:broader+ roll-up, and would do so
        # non-deterministically since rdflib does not order objects().
        self._terms: dict[URIRef, tuple[URIRef, str, tuple[URIRef, ...]]] = {}
        for rank in RANKS:
            rank_class = NPC[rank]
            for term in graph.subjects(RDF.type, rank_class):
                if not isinstance(term, URIRef):
                    continue
                label = next((str(o) for o in graph.objects(term, RDFS.label)), None)
                if label is None:
                    continue
                broader = tuple(sorted(
                    (o for o in graph.objects(term, SKOS.broader) if isinstance(o, URIRef)),
                    key=str,
                ))  # sorted so the emitted graph is byte-reproducible across runs
                self._by_label[(rank, label.strip().casefold())] = term
                self._terms[term] = (rank_class, label.strip(), broader)
        # Labels already warned about, so version skew is reported once, not once per row.
        self._warned: set[tuple[str, str]] = set()

    def __len__(self) -> int:
        return len(self._terms)

    def resolve(self, rank: str, label: str) -> URIRef:
        """Return the ``npc:`` IRI for ``label`` at ``rank``, minting one if unvendored.

        Args:
            rank: One of :data:`RANKS`. Required, not optional context -- see the module
                docstring on the two cross-rank collisions.
            label: The NPClassifier label exactly as SIRIUS wrote it.

        Returns:
            The vendored IRI when EMI has the term, otherwise one minted by EMI's own rule.
        """
        if rank not in RANKS:
            raise ValueError(f"Unknown NPClassifier rank {rank!r}; expected one of {RANKS}.")
        key = (rank, label.strip().casefold())
        term = self._by_label.get(key)
        if term is not None:
            return term
        if key not in self._warned:
            self._warned.add(key)
            _LOGGER.warning(
                "NPClassifier %s %r is not in the vendored EMI vocabulary (%s); minting %s "
                "by EMI's own rule. Refresh docs/vocab/EMI-vocab.owl if this list grows.",
                rank.lower(), label, _EMI_VOCAB_PATH.name, _mint(label),
            )
        return _mint(label)

    def describe(self, term: URIRef) -> Iterator[tuple[URIRef, URIRef, object]]:
        """Yield the triples that make ``term`` self-describing, ancestors included.

        Without these the graph carries bare IRIs: no label to group by and no
        ``skos:broader`` chain to roll up, so the obvious queries return empty rather than
        failing loudly. Minted (unvendored) terms describe as nothing -- there is nothing
        truthful to say about them.

        Walks the ancestry as a DAG rather than a chain, since a term may have two parents;
        a tree walk would drop the second and under-count roll-ups.
        """
        seen: set[URIRef] = set()
        pending: list[URIRef] = [term]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            entry = self._terms.get(current)
            if entry is None:
                continue  # minted, unvendored term: nothing truthful to say about it
            rank_class, label, broader = entry
            yield (current, RDF.type, rank_class)
            yield (current, RDFS.label, Literal(label))
            for parent in broader:
                yield (current, SKOS.broader, parent)
                pending.append(parent)


@lru_cache(maxsize=1)
def npc_vocabulary() -> NpcVocabulary:
    """Return the process-wide NPClassifier vocabulary, parsing it on first use."""
    return NpcVocabulary()
