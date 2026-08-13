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
from rdflib import Graph
from rdflib.namespace import OWL, RDF, RDFS

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.chemical_annotation import MS2ChemicalAnnotation
from enpkg.monolith.data.otl_class import Match, Taxon
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.rdf import AnalysisSerializer
from enpkg.monolith.rdf.namespaces import ENPKG

_TTL_PATH = Path(__file__).resolve().parents[3] / "docs" / "vocab" / "enpkg.ttl"


def _build_maximal_analysis(make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation):
    """An Analysis exercising every enpkg:-emitting branch of the serializer at
    once: an MS1 adduct cluster (anchor + 2 satellites), an MS1 candidate
    coupled to an MS2 match, SIRIUS candidates, an OTT match, and a molecular
    network with both an edge and a >=2-member component — plus sample fields
    (source_id, filenames) that only ever come from user metadata."""
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
            n_matched_peaks=5,
            pathway_scores=np.array([]),
            superclass_scores=np.array([]),
            class_scores=np.array([]),
        )
    ]

    sirius_spectrum.sirius_annotations = [
        make_sirius_annotation(rank=1),
        make_sirius_annotation(rank=2, inchikey_2d="SKELETON00002X"),
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
        include_network=True,
        include_ions=True,
        min_relative_intensity=0.1,
        max_ions_per_spectrum=1,
    )
    serializer.add_analysis(analysis)
    return serializer


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


def test_live_and_target_tags_match_what_is_actually_emitted(
    make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
):
    """Every enpkg.ttl term is tagged ``[live]`` or ``[target]`` in its rdfs:comment,
    and those tags must mean what they say: ``[live]`` exactly when the serializer
    emits the term, ``[target]`` exactly when it does not (yet).

    The tags are load-bearing, not decoration — docs/RDF_KG_DATA_MODEL.md (as-built)
    and docs/_static/MAIN_SCHEMA.mmd (target) are split along precisely this line, so
    a stale tag silently makes one of those two documents wrong. Stronger than the
    drift test above, which only checks emitted ⊆ declared and so cannot notice a
    term that was wired into the serializer while its comment still says [target].
    """
    serializer = _serialize_maximal(
        make_spectrum, make_lotus, make_recipe, make_adduct, make_sirius_annotation
    )
    # Phase 3 inlines the whole vocabulary into every graph, so the declarations are
    # present regardless of what the data used. Subtracting a data-free serializer's
    # graph leaves only the triples this analysis actually contributed.
    baseline = AnalysisSerializer()
    data_only = Graph()
    for triple in set(serializer.graph) - set(baseline.graph):
        data_only.add(triple)
    emitted = _enpkg_local_names(data_only)

    vocabulary = Graph()
    vocabulary.parse(_TTL_PATH, format="turtle")
    declared = (
        set(vocabulary.subjects(RDF.type, OWL.Class))
        | set(vocabulary.subjects(RDF.type, OWL.ObjectProperty))
        | set(vocabulary.subjects(RDF.type, OWL.DatatypeProperty))
    )

    ns = str(ENPKG)
    tagged_live, untagged = set(), []
    for term in declared:
        comment = " ".join(str(c) for c in vocabulary.objects(term, RDFS.comment))
        name = str(term)[len(ns):]
        if "[live]" in comment:
            tagged_live.add(name)
        elif "[target]" not in comment:
            untagged.append(name)

    assert not sorted(untagged), (
        f"{len(untagged)} term(s) in enpkg.ttl carry neither a [live] nor a [target] "
        f"tag in their rdfs:comment: {sorted(untagged)}"
    )
    assert not sorted(tagged_live - emitted), (
        "term(s) tagged [live] in enpkg.ttl that the serializer never emitted: "
        f"{sorted(tagged_live - emitted)}. Either the tag should be [target], or the "
        "fixture above stopped covering that code path."
    )
    assert not sorted(emitted - tagged_live), (
        "term(s) the serializer emits that enpkg.ttl still tags [target]: "
        f"{sorted(emitted - tagged_live)}. Retag them [live]."
    )
