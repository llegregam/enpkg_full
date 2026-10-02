"""Phase 4 drift test (docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md, Group G).

Serializes an Analysis that exercises every enpkg:-emitting code path in
AnalysisSerializer, then asserts every enpkg: IRI that shows up in the output
graph is declared in docs/vocab/enpkg.ttl. Catches the "declared in code but
never written into the vocabulary" gap mechanically (this is exactly how the
missing enpkg:SpectralAnnotation/enpkg:SiriusAnnotation class declarations
were found while authoring the TTL) instead of relying on a hand-maintained
inventory, which is what let that gap and Group E's 8-term undercount happen
in the first place.
"""

from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.chemical_annotation import LibraryStructure, MS2ChemicalAnnotation
from enpkg.monolith.data.otl_class import Match, Taxon
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.rdf import AnalysisSerializer
from enpkg.monolith.rdf.namespaces import EMI, ENPKG, VS

_VOCAB_DIR = Path(__file__).resolve().parents[3] / "docs" / "vocab"
_TTL_PATH = _VOCAB_DIR / "enpkg.ttl"
_EMI_PATH = _VOCAB_DIR / "EMI-vocab.owl"
# The values the SemWeb Vocab Status vocabulary documents for vs:term_status.
_TERM_STATUSES = frozenset({"unstable", "testing", "stable", "archaic"})


def _build_maximal_analysis(make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation):
    """An Analysis exercising every enpkg:-emitting branch of the serializer at
    once: an MS1 adduct cluster (anchor + 2 satellites), an MS1 candidate
    coupled to an MS2 match, an MS2 match only the spectral library knows,
    SIRIUS candidates, an OTT match, and a molecular network with both an edge
    and a >=2-member component — plus sample fields (source_id, filenames)
    that only ever come from user metadata."""
    anchor = make_spectrum(feature_id=1, precursor_mz=201.0)
    sat_na = make_spectrum(feature_id=2, precursor_mz=223.0)
    sat_k = make_spectrum(feature_id=3, precursor_mz=239.0)
    sirius_spectrum = make_spectrum(feature_id=4, precursor_mz=250.0)

    for spectrum in (anchor, sat_na, sat_k):
        spectrum.ms1_cluster_id = 0
        spectrum.ms1_cluster_connectivity = 3
        spectrum.ms1_cluster_intensity_coverage = 0.97
        spectrum.ms1_cluster_count_coverage = 0.75
    anchor.ms1_cluster_role = "anchor"
    anchor.ms1_assigned_recipe = make_recipe(ingredients={"proton": 1})
    sat_na.ms1_cluster_role = "satellite"
    sat_na.ms1_assigned_recipe = make_recipe(ingredients={"sodium": 1})
    sat_k.ms1_cluster_role = "satellite"
    sat_k.ms1_assigned_recipe = make_recipe(ingredients={"potassium": 1})

    matched_ik = "MATCHEDCOMPND1"  # 14 chars
    lotus = make_lotus(structure_inchikey=f"{matched_ik}-UHFFFAOYSA-N")
    adduct = make_adduct(lotus=[lotus], recipe=make_recipe(ingredients={"proton": 1}))
    anchor.ms1_annotations = [adduct]
    anchor.ms2_annotations = [
        MS2ChemicalAnnotation(
            source="ISDB",
            short_inchikey=matched_ik,
            score=0.9,
            algorithm="cosine_greedy",
            n_matched_peaks=5,
            pathway_scores=np.array([]),
            superclass_scores=np.array([]),
            class_scores=np.array([]),
        )
    ]

    sirius_spectrum.sirius_annotations = [
        make_sirius_annotation(rank=1, sirius_version="6.3.4"),
        make_sirius_annotation(rank=2, inchikey_2d="SKELETON00002X", sirius_version="6.3.4"),
    ]
    sirius_spectrum.ms2_annotations = [
        MS2ChemicalAnnotation(
            source="LIB:1.0",
            queried_against="LIB:1.0",
            short_inchikey="LIBRARYONLYIKX",
            score=0.8,
            algorithm="cosine_hungarian",
            n_matched_peaks=6,
            pathway_scores=np.array([]),
            superclass_scores=np.array([]),
            class_scores=np.array([]),
            library_structure=LibraryStructure(
                inchikey="LIBRARYONLYIKX-UHFFFAOYSA-N",
                inchi="InChI=1S/C2H6/c1-2/h1-2H3",
                smiles="CC",
                molecular_formula="C2H6",
                compound_name="Library-only compound",
                npc_pathway="Alkaloids",
                npc_superclass=None,
                npc_class=None,
                classyfire_superclass="Organoheterocyclic compounds",
                classyfire_class="Indoles",
                classyfire_subclass="Indolines",
            ),
        )
    ]

    ott_taxon = Taxon(
        flags=[], is_suppressed=False, is_suppressed_from_synth=False,
        name="Artemisia annua", open_tree_taxon_id=333, rank="species",
        source="ott", synonyms=[], tax_sources=[], unique_name="Artemisia annua",
    )
    ott_match = Match(
        is_approximate_match=False, is_synonym=False, matched_name="Artemisia annua",
        nomenclature_code="ICN", score=0.99, search_string="Artemisia annua", taxon=ott_taxon,
    )

    network = nx.Graph()
    network.add_nodes_from([1, 2, 3, 4])
    network.add_edge(1, 2, weight=0.9)
    network.add_edge(2, 3, weight=0.85)

    metadata = SampleMetadata(
        sample_id="S1", source_taxon="Artemisia annua", source_id="SRC1",
        sample_filename_pos="pos.mzml", sample_filename_neg="neg.mzml",
    )

    return Analysis(
        run_name="DRIFT",
        spectra=(anchor, sat_na, sat_k, sirius_spectrum),
        metadata=metadata,
        ionization_mode="pos",
        ott_matches=[ott_match],
        molecular_network=network,
    )


def _enpkg_local_names(graph: Graph) -> set[str]:
    """Every enpkg: local name appearing anywhere in ``graph``.

    Safe to scan every triple position: only vocabulary terms (classes,
    properties) are minted under the enpkg: namespace — every instance node
    the serializer mints lives under EMI_RES (see namespaces.py) — so there is
    no risk of picking up a data IRI here.
    """
    ns = str(ENPKG)
    return {str(term)[len(ns):] for triple in graph for term in triple if str(term).startswith(ns)}


def _serialize_maximal(make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation):
    """Serialize the maximal fixture with every gated layer switched on."""
    analysis = _build_maximal_analysis(
        make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
    )
    serializer = AnalysisSerializer(
        include_ions=True,
        min_relative_intensity=0.1,
        max_ions_per_spectrum=1,
    )
    serializer.add_analysis(analysis)
    return serializer


def _data_only(serializer: AnalysisSerializer) -> Graph:
    """The triples an analysis contributed, without the inlined vocabulary.

    Every graph carries the whole of enpkg.ttl, so subtracting a data-free
    serializer's graph leaves only what the serializer emitted for the data.
    """
    data_only = Graph()
    for triple in set(serializer.graph) - set(AnalysisSerializer().graph):
        data_only.add(triple)
    return data_only


@pytest.fixture(scope="module")
def emi_vocabulary() -> Graph:
    """The vendored EMI ontology (~1.5 s to parse, so once per module)."""
    return Graph().parse(_EMI_PATH)


def test_serializer_enpkg_terms_are_all_declared_in_the_vocabulary(
    make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
):
    serializer = _serialize_maximal(
        make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
    )
    emitted = _enpkg_local_names(serializer.graph)
    assert emitted, "fixture emitted no enpkg: terms — it stopped exercising the serializer"

    vocabulary = Graph()
    vocabulary.parse(_TTL_PATH, format="turtle")
    declared = _enpkg_local_names(vocabulary)

    missing = sorted(emitted - declared)
    assert not missing, (
        f"{len(missing)} enpkg: term(s) emitted by the serializer are not declared "
        f"in docs/vocab/enpkg.ttl: {missing}"
    )


def _declared_terms(vocabulary: Graph) -> set[URIRef]:
    """Every class and property enpkg.ttl declares in the enpkg: namespace."""
    ns = str(ENPKG)
    return {
        term
        for kind in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty)
        for term in vocabulary.subjects(RDF.type, kind)
        if str(term).startswith(ns)
    }


def test_term_status_matches_what_is_actually_emitted(
    make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
):
    """Every enpkg.ttl term carries exactly one ``vs:term_status``, and it agrees with
    the serializer: a term is emitted exactly when its status is ``testing`` or
    ``stable``, and not emitted when it is ``unstable`` (declared, not yet in use) or
    ``archaic`` (retired).

    docs/RDF_KG_DATA_MODEL.md (what an export contains) and docs/_static/MAIN_SCHEMA.mmd
    (where the schema is going) are split along this line, so a stale status makes one
    of them wrong. Stronger than the drift test above, which only checks emitted ⊆
    declared and so cannot notice a term that was wired into the serializer while its
    status still says ``unstable``.
    """
    serializer = _serialize_maximal(
        make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
    )
    emitted = _enpkg_local_names(_data_only(serializer))

    vocabulary = Graph().parse(_TTL_PATH, format="turtle")
    ns = str(ENPKG)
    statuses = {
        str(term)[len(ns):]: [str(s) for s in vocabulary.objects(term, VS.term_status)]
        for term in _declared_terms(vocabulary)
    }

    malformed = sorted(
        name for name, values in statuses.items()
        if len(values) != 1 or values[0] not in _TERM_STATUSES
    )
    assert not malformed, (
        f"term(s) without exactly one vs:term_status from {sorted(_TERM_STATUSES)}: {malformed}"
    )
    in_use = {name for name, (status,) in statuses.items() if status in ("testing", "stable")}
    assert not sorted(in_use - emitted), (
        "term(s) whose vs:term_status says they are in use that the serializer never "
        f"emitted: {sorted(in_use - emitted)}. Either the status should be unstable, or "
        "the fixture above stopped covering that code path."
    )
    assert not sorted(emitted - in_use), (
        "term(s) the serializer emits whose vs:term_status is still unstable or archaic: "
        f"{sorted(emitted - in_use)}."
    )


def test_every_term_has_a_label_and_a_definition():
    """A term's rdfs:label names it and its rdfs:comment defines it, for readers of the
    published vocabulary who never see this codebase."""
    vocabulary = Graph().parse(_TTL_PATH, format="turtle")
    ns = str(ENPKG)
    unlabelled, undefined = [], []
    for term in _declared_terms(vocabulary):
        name = str(term)[len(ns):]
        if not any(str(label).strip() for label in vocabulary.objects(term, RDFS.label)):
            unlabelled.append(name)
        if not any(str(comment).strip() for comment in vocabulary.objects(term, RDFS.comment)):
            undefined.append(name)
    assert not unlabelled, f"term(s) without an rdfs:label: {sorted(unlabelled)}"
    assert not undefined, f"term(s) without an rdfs:comment: {sorted(undefined)}"


def test_emitted_enpkg_literals_carry_their_declared_range(
    make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
):
    """Every enpkg: literal is typed exactly as its property's rdfs:range says.

    A literal whose datatype differs from the range is not a formatting nuance: OWL 2
    treats xsd:integer and xsd:double as disjoint value spaces, so a reasoner reads
    ``"3"^^xsd:integer`` under a property with range xsd:double as a contradiction and
    reports the whole export inconsistent. rdflib picks the datatype from the Python
    type of the value, so a field that arrives as an int in one run and a float in the
    next changes the emitted datatype without any code change.
    """
    serializer = _serialize_maximal(
        make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
    )
    vocabulary = Graph().parse(_TTL_PATH, format="turtle")
    ranges = {
        prop: rng
        for prop in vocabulary.subjects(RDF.type, OWL.DatatypeProperty)
        for rng in vocabulary.objects(prop, RDFS.range)
    }

    wrong = set()
    for _, predicate, obj in _data_only(serializer):
        if predicate in ranges and isinstance(obj, Literal):
            datatype = obj.datatype or (XSD.string if obj.language is None else RDF.langString)
            if datatype != ranges[predicate]:
                wrong.add((predicate.n3(vocabulary.namespace_manager),
                           datatype.n3(vocabulary.namespace_manager),
                           ranges[predicate].n3(vocabulary.namespace_manager)))
    assert not wrong, (
        "enpkg: literal(s) emitted with a datatype other than the declared rdfs:range "
        f"(property, emitted, declared): {sorted(wrong)}"
    )


def test_emitted_emi_terms_exist_in_the_vendored_emi_ontology(
    make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation,
    emi_vocabulary,
):
    """Every emi: predicate and rdf:type the serializer emits is declared by EMI.

    The enpkg: drift test cannot see these: emi: terms are not ours to declare, so a
    misspelt or invented one (an emi: IRI EMI never defined) reaches the export as a
    bare IRI nobody can look up.
    """
    serializer = _serialize_maximal(
        make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
    )
    ns = str(EMI)
    used = set()
    for _, predicate, obj in _data_only(serializer):
        if str(predicate).startswith(ns):
            used.add(predicate)
        if predicate == RDF.type and isinstance(obj, URIRef) and str(obj).startswith(ns):
            used.add(obj)
    assert used, "fixture emitted no emi: terms — it stopped exercising the serializer"

    declared = set(emi_vocabulary.subjects(RDF.type, None))
    missing = sorted(str(term)[len(ns):] for term in used - declared)
    assert not missing, f"emi: term(s) emitted that EMI-vocab.owl does not declare: {missing}"


def test_vocabulary_imports_emi_by_its_ontology_iri(emi_vocabulary):
    """``owl:imports`` names an ontology by its ontology IRI, so the import must match
    the IRI EMI declares for itself, which is not the ``emi:`` term namespace."""
    vocabulary = Graph().parse(_TTL_PATH, format="turtle")
    emi_ontology = next(emi_vocabulary.subjects(RDF.type, OWL.Ontology))
    imports = set(vocabulary.objects(None, OWL.imports))
    assert imports == {emi_ontology}, (
        f"enpkg.ttl imports {sorted(map(str, imports))}; EMI's ontology IRI is {emi_ontology}"
    )


@pytest.mark.parametrize("namespace", [RDF, RDFS, OWL, SKOS], ids=["rdf", "rdfs", "owl", "skos"])
def test_vocabulary_uses_only_terms_its_meta_vocabularies_define(namespace):
    """Every rdf:/rdfs:/owl:/skos: IRI in enpkg.ttl is a term that vocabulary defines.

    A Turtle parser accepts any local name under a known prefix, so an invented
    predicate such as ``rdfs:definedBy`` (the RDFS term is ``rdfs:isDefinedBy``)
    parses cleanly and is simply ignored by every tool that reads the file.
    """
    vocabulary = Graph().parse(_TTL_PATH, format="turtle")
    ns = str(namespace)
    used = {term for triple in vocabulary for term in triple
            if isinstance(term, URIRef) and str(term).startswith(ns)}
    undefined = sorted(str(term)[len(ns):] for term in used if term not in namespace)
    assert not undefined, f"enpkg.ttl uses undefined {ns} term(s): {undefined}"
