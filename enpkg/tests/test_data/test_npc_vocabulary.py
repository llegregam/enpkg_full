"""Unit tests for NPClassifier label -> npc: IRI resolution.

Reads the vendored ``docs/vocab/EMI-vocab.owl`` (no network, no DuckDB), so it belongs in
the fast default suite. The vocabulary is parsed once per process and cached, so the
~1.5 s load is paid by the first test only.
"""

import logging

from rdflib import Literal, RDFS, URIRef
from rdflib.namespace import RDF, SKOS

from enpkg.monolith.rdf.namespaces import NPC
from enpkg.monolith.rdf.npc_vocabulary import NpcVocabulary, npc_vocabulary


def test_vocabulary_is_cached_across_calls():
    """G4: the parse must be paid once per process, not once per serializer.

    ``batch_runner`` builds one serializer per experiment; an uncached load would add the
    full parse to every one of them.
    """
    assert npc_vocabulary() is npc_vocabulary()


def test_resolves_all_three_ranks():
    vocabulary = npc_vocabulary()
    assert vocabulary.resolve("Pathway", "Terpenoids") == NPC["TERPENOIDS"]
    assert vocabulary.resolve("Superclass", "Monoterpenoids") == NPC["MONOTERPENOIDS"]
    assert vocabulary.resolve("Class", "Iridoids monoterpenoids") == NPC["IRIDOIDS_MONOTERPENOIDS"]


def test_resolution_is_case_insensitive():
    vocabulary = npc_vocabulary()
    assert vocabulary.resolve("Pathway", "terpenoids") == NPC["TERPENOIDS"]
    assert vocabulary.resolve("Pathway", "  Terpenoids  ") == NPC["TERPENOIDS"]


def test_same_label_at_two_ranks_resolves_differently():
    """G3: the reason the lookup key is (rank, label) and not label alone.

    EMI disambiguates the Class reading with a ``_CLASS`` suffix. A naive
    ``upper().replace(" ", "_")`` files the Class under its own Superclass — silently, and
    then double-counts it under ``skos:broader+``.
    """
    vocabulary = npc_vocabulary()
    for label in ("Miscellaneous alkaloids", "Miscellaneous polyketides"):
        superclass = vocabulary.resolve("Superclass", label)
        chemical_class = vocabulary.resolve("Class", label)
        assert superclass != chemical_class, label
        assert str(chemical_class).endswith("_CLASS"), label


def test_resolves_percent_encoded_and_non_ascii_terms():
    """G3: npc: locals are percent-encoded upper-cased labels, not plain ASCII."""
    vocabulary = npc_vocabulary()
    assert vocabulary.resolve("Superclass", "Phenolic acids (C6-C1)") == NPC["PHENOLIC_ACIDS_%28C6-C1%29"]
    assert vocabulary.resolve("Superclass", "\u03b2-lactams") == NPC["%CE%92-LACTAMS"]
    assert vocabulary.resolve("Class", "Carotenoids (C40, \u03b2-\u03b2)") == NPC[
        "CAROTENOIDS_%28C40%2C_%CE%92-%CE%92%29"
    ]


def test_unvendored_label_is_minted_and_warned(caplog):
    """G5: SIRIUS ships a newer NPClassifier than EMI vendored."""
    vocabulary = NpcVocabulary()  # a fresh instance, so the warn-once set is empty
    with caplog.at_level(logging.WARNING):
        minted = vocabulary.resolve("Class", "Purine nucleosides")
    assert minted == NPC["PURINE_NUCLEOSIDES"]
    assert "not in the vendored EMI vocabulary" in caplog.text


def test_unvendored_label_warns_only_once(caplog):
    """Version skew is reported once, not once per row -- 1376 rows carry this label."""
    vocabulary = NpcVocabulary()
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            vocabulary.resolve("Class", "Purine nucleosides")
    assert caplog.text.count("not in the vendored EMI vocabulary") == 1


def test_unknown_rank_raises():
    vocabulary = npc_vocabulary()
    try:
        vocabulary.resolve("Kingdom", "Terpenoids")
    except ValueError as error:
        assert "Unknown NPClassifier rank" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for an unknown rank")


def test_describe_yields_type_label_and_ancestry():
    """G2: what makes an emitted npc: IRI self-describing in the output graph."""
    vocabulary = npc_vocabulary()
    term = vocabulary.resolve("Class", "Iridoids monoterpenoids")
    triples = set(vocabulary.describe(term))
    assert (term, RDF.type, NPC["Class"]) in triples
    assert (term, RDFS.label, Literal("Iridoids monoterpenoids")) in triples
    # the chain must reach all the way up to the Pathway, or roll-up queries stop short
    assert (term, SKOS.broader, NPC["MONOTERPENOIDS"]) in triples
    assert (NPC["MONOTERPENOIDS"], SKOS.broader, NPC["TERPENOIDS"]) in triples
    assert (NPC["TERPENOIDS"], RDF.type, NPC["Pathway"]) in triples


def test_describe_keeps_every_parent_of_a_multi_parent_term():
    """NPClassifier's taxonomy is a DAG, not a tree.

    23 of the 770 terms have two parents -- Meroterpenoids is both a polyketide and a
    terpenoid. Walking the ancestry as a chain keeps whichever parent rdflib happened to
    yield first, which both under-counts ``skos:broader+`` roll-ups and makes the emitted
    graph depend on iteration order.
    """
    vocabulary = npc_vocabulary()
    term = vocabulary.resolve("Superclass", "Meroterpenoids")
    parents = {o for s, p, o in vocabulary.describe(term) if p == SKOS.broader and s == term}
    assert parents == {NPC["POLYKETIDES"], NPC["TERPENOIDS"]}


def test_describe_of_a_minted_term_yields_nothing():
    """There is nothing truthful to say about a term the vocabulary does not have."""
    vocabulary = npc_vocabulary()
    assert list(vocabulary.describe(vocabulary.resolve("Class", "Purine nucleosides"))) == []
    assert list(vocabulary.describe(URIRef("https://example.org/not-a-term"))) == []


def test_vocabulary_covers_the_expected_term_count():
    """A canary on the vendored file: a refresh that changes this should be deliberate."""
    vocabulary = npc_vocabulary()
    assert len(vocabulary) == 770
