"""Smoke tests for the RDF serializer (spine + idempotency + round-trip)."""

import networkx as nx
import numpy as np
import pytest
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.chemical_annotation import MS2ChemicalAnnotation
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.rdf import AnalysisSerializer, serialize_to_turtle
from enpkg.monolith.rdf.namespaces import CHEMROF, EMI, EMI_RES, ENPKG, NPC
from enpkg.monolith.rdf.uris import AnalysisURIs, CompoundURIs


def test_serialize_emits_the_analysis_node(make_analysis):
    analysis = make_analysis(run_name="RUNX", n_spectra=2)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    assert len(serializer.graph) > 0
    assert AnalysisURIs.analysis_uri(analysis) in set(serializer.graph.subjects())


def test_massive_doi_is_a_uriref_not_a_literal(make_analysis):
    """emi:hasMassiveDOI is declared owl:ObjectProperty in EMI — the object must
    be a resolvable URI, not a literal accession string."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    analysis = analysis.model_copy(
        update={"metadata": analysis.metadata.model_copy(update={"massive_id": "MSV000087728"})}
    )
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    uri = AnalysisURIs.analysis_uri(analysis)
    massive = serializer.graph.value(uri, EMI.hasMassiveDOI)
    assert isinstance(massive, URIRef)
    assert str(massive) == "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=MSV000087728"


def test_massive_doi_keeps_a_full_url_as_is(make_analysis):
    """A metadata sheet that already stores the full MassIVE URL isn't double-prefixed."""
    full_url = "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=MSV000099999"
    analysis = make_analysis(run_name="RUNZ", n_spectra=1)
    analysis = analysis.model_copy(
        update={"metadata": analysis.metadata.model_copy(update={"massive_id": full_url})}
    )
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    uri = AnalysisURIs.analysis_uri(analysis)
    assert str(serializer.graph.value(uri, EMI.hasMassiveDOI)) == full_url


def test_massive_doi_absent_when_massive_id_unset(make_analysis):
    analysis = make_analysis(run_name="RUNY", n_spectra=1)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    uri = AnalysisURIs.analysis_uri(analysis)
    assert serializer.graph.value(uri, EMI.hasMassiveDOI) is None


def test_add_analysis_is_idempotent(make_analysis):
    """RDF graphs are triple sets, so re-adding the same analysis is a no-op."""
    analysis = make_analysis(n_spectra=2)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    first = len(serializer.graph)
    serializer.add_analysis(analysis)
    assert len(serializer.graph) == first


def test_turtle_roundtrips(make_analysis):
    analysis = make_analysis(n_spectra=2)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    turtle = serializer.graph.serialize(format="turtle")
    reparsed = Graph().parse(data=turtle, format="turtle")
    assert len(reparsed) == len(serializer.graph)


def test_serialize_to_turtle_writes_a_file(make_analysis, tmp_path):
    analysis = make_analysis(n_spectra=1)
    out = tmp_path / "out.ttl"
    serialize_to_turtle(analysis, str(out))
    assert out.exists() and out.stat().st_size > 0


def _stamp_one_cluster(analysis, make_recipe):
    """Stamp a 3-feature cluster (anchor + 2 satellites) + 1 singleton on the
    analysis' spectra, as the MS1 graph enhancer would. Returns the pieces the
    assertions need."""
    anchor, sat_na, sat_k, singleton = analysis.spectra
    recipes = {
        "anchor": make_recipe(ingredients={"proton": 1}),
        "na": make_recipe(ingredients={"sodium": 1}),
        "k": make_recipe(ingredients={"potassium": 1}),
    }
    for spectrum in (anchor, sat_na, sat_k):
        spectrum.ms1_cluster_id = 0
        spectrum.ms1_cluster_connectivity = 3
        spectrum.ms1_cluster_intensity_coverage = 0.9719
        spectrum.ms1_cluster_count_coverage = 0.75
    anchor.ms1_cluster_role = "anchor"
    anchor.ms1_assigned_recipe = recipes["anchor"]
    sat_na.ms1_cluster_role = "satellite"
    sat_na.ms1_assigned_recipe = recipes["na"]
    sat_k.ms1_cluster_role = "satellite"
    sat_k.ms1_assigned_recipe = recipes["k"]
    # `singleton` keeps its defaults (ms1_cluster_id is None).
    return anchor, sat_na, sat_k, singleton, recipes


def test_adduct_clusters_are_serialized(make_analysis, make_recipe):
    analysis = make_analysis(run_name="RUNX", n_spectra=4)
    anchor, sat_na, sat_k, singleton, recipes = _stamp_one_cluster(analysis, make_recipe)

    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    cluster_uri = AnalysisURIs.adduct_cluster_uri(analysis, 0)
    anchor_uri = AnalysisURIs.spectrum_uri(analysis, anchor)

    # Exactly one cluster node (the singleton makes none), with its indices.
    assert set(g.subjects(RDF.type, ENPKG.AdductCluster)) == {cluster_uri}
    assert (cluster_uri, ENPKG.clusterConnectivity, Literal(3)) in g
    assert (cluster_uri, ENPKG.clusterIntensityCoverage, Literal(0.9719)) in g
    assert (cluster_uri, ENPKG.clusterCountCoverage, Literal(0.75)) in g

    # Linked off the feature set, anchor identified, all three members present.
    assert (AnalysisURIs.featureset_uri(analysis), ENPKG.hasAdductCluster, cluster_uri) in g
    assert (cluster_uri, ENPKG.hasAnchor, anchor_uri) in g
    assert set(g.objects(cluster_uri, ENPKG.hasClusterMember)) == {
        AnalysisURIs.spectrum_uri(analysis, s) for s in (anchor, sat_na, sat_k)
    }

    # Per-feature resolution: role, back-link, and the resolved form as a literal.
    assert (anchor_uri, ENPKG.clusterRole, Literal("anchor")) in g
    assert (anchor_uri, ENPKG.inAdductCluster, cluster_uri) in g
    assert (anchor_uri, ENPKG.resolvedAdduct, Literal("[M+H]+")) in g
    sat_na_uri = AnalysisURIs.spectrum_uri(analysis, sat_na)
    sat_k_uri = AnalysisURIs.spectrum_uri(analysis, sat_k)
    assert (sat_na_uri, ENPKG.clusterRole, Literal("satellite")) in g
    assert (sat_na_uri, ENPKG.resolvedAdduct, Literal("[M+Na]+")) in g
    assert (sat_k_uri, ENPKG.resolvedAdduct, Literal("[M+K]+")) in g
    # No parallel edge / bare recipe node for the resolved form.
    assert list(g.objects(anchor_uri, ENPKG.hasResolvedAdduct)) == []

    # The singleton gets no cluster node and no cluster triples.
    singleton_uri = AnalysisURIs.spectrum_uri(analysis, singleton)
    assert list(g.objects(singleton_uri, ENPKG.clusterRole)) == []
    assert list(g.objects(singleton_uri, ENPKG.inAdductCluster)) == []
    assert list(g.objects(singleton_uri, ENPKG.resolvedAdduct)) == []
    assert (AnalysisURIs.adduct_cluster_uri(analysis, 1), RDF.type, ENPKG.AdductCluster) not in g

    # Re-adding is idempotent (RDF is a triple set).
    before = len(g)
    serializer.add_analysis(analysis)
    assert len(g) == before


def test_adduct_clusters_can_be_disabled(make_analysis, make_recipe):
    analysis = make_analysis(run_name="RUNX", n_spectra=4)
    _stamp_one_cluster(analysis, make_recipe)

    serializer = AnalysisSerializer(include_adduct_clusters=False)
    serializer.add_analysis(analysis)
    g = serializer.graph

    # No cluster *instances* (the class declaration itself is unaffected).
    assert set(g.subjects(RDF.type, ENPKG.AdductCluster)) == set()
    anchor_uri = AnalysisURIs.spectrum_uri(analysis, analysis.spectra[0])
    assert list(g.objects(anchor_uri, ENPKG.clusterRole)) == []


def _ms2(short_inchikey, score=0.9):
    """A minimal MS2 spectral-library match (no NPC scores → cosine-ranked)."""
    return MS2ChemicalAnnotation(
        source="ISDB",
        short_inchikey=short_inchikey,
        score=score,
        pathway_scores=np.array([]),
        superclass_scores=np.array([]),
        class_scores=np.array([]),
    )


def _short_ik(seed):
    """A distinct valid-looking 14-char short InChIKey."""
    return f"{seed:0<14}"[:14].upper().replace("0", "A")


def test_adduct_carries_neutral_and_ion_mass(make_analysis, make_adduct, make_lotus, make_recipe):
    """adductNeutralMass (the matched candidate's own exact mass) and adductMass
    (the ion mass derived from it via the recipe) are two consistent views of the
    same hypothesis — both must be present, and mutually derivable."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    lotus = make_lotus(structure_exact_mass=30.04)
    recipe = make_recipe(ingredients={"proton": 1})
    adduct = make_adduct(lotus=[lotus], recipe=recipe)
    spectrum.ms1_annotations = [adduct]

    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, adduct)
    assert g.value(uri, ENPKG.adductNeutralMass) == Literal(30.04)
    assert g.value(uri, ENPKG.adductMass) == Literal(adduct.adduct_mass)
    # Consistency: the ion mass is exactly what the recipe derives from the neutral mass.
    assert adduct.adduct_mass == recipe.compute_adduct_mass(adduct.neutral_mass)


def test_ms1_candidate_structure_uses_sibling_predicate(make_analysis, make_adduct):
    """MS1 candidates attach via enpkg:hasCandidateStructure (mass-coincidence hit),
    not emi:hasChemicalStructure (confirmed identification, used by MS2/SIRIUS) — see §1b."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    adduct = make_adduct()
    spectrum.ms1_annotations = [adduct]

    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, adduct)
    assert (uri, ENPKG.hasCandidateStructure, None) in g
    assert (uri, EMI.hasChemicalStructure, None) not in g


def test_ms2_couples_and_prunes_ms1(make_analysis, make_adduct, make_lotus, make_recipe):
    """An MS2-identified feature keeps only MS1 adducts proposing the matched
    compound, each linked from the MS2 annotation; mass-coincidence adducts drop."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]

    matched_ik = "MATCHEDCOMPND1"       # 14 chars
    coincidence_ik = "COINCIDENCE123"   # 14 chars
    matched_lotus = make_lotus(structure_inchikey=f"{matched_ik}-UHFFFAOYSA-N")
    coincidence_lotus = make_lotus(structure_inchikey=f"{coincidence_ik}-UHFFFAOYSA-N")

    # One MS1 adduct proposes the MS2 compound; one is a pure mass coincidence.
    matched_adduct = make_adduct(lotus=[matched_lotus], recipe=make_recipe(ingredients={"proton": 1}))
    coincidence_adduct = make_adduct(
        lotus=[coincidence_lotus], recipe=make_recipe(ingredients={"sodium": 1})
    )
    spectrum.ms1_annotations = [matched_adduct, coincidence_adduct]
    spectrum.ms2_annotations = [_ms2(matched_ik)]

    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    matched_uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, matched_adduct)
    coincidence_uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, coincidence_adduct)
    ms2_uri = AnalysisURIs.ms2_annotation_uri(analysis, spectrum, spectrum.ms2_annotations[0])

    # Corresponding adduct emitted and coupled; coincidence adduct dropped entirely.
    assert (matched_uri, RDF.type, ENPKG.AdductAnnotation) in g
    assert (ms2_uri, ENPKG.hasCorrespondingAdduct, matched_uri) in g
    assert (coincidence_uri, RDF.type, ENPKG.AdductAnnotation) not in g
    assert list(g.objects(ms2_uri, ENPKG.hasCorrespondingAdduct)) == [matched_uri]


def test_ms2_keeps_all_corresponding_adduct_forms(
    make_analysis, make_adduct, make_lotus, make_recipe
):
    """Correspondence is compound-level: every adduct *form* of the matched
    compound is kept and linked, not just one — and top_k does not cut them."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]

    matched_ik = "MATCHEDCOMPND1"
    lotus = make_lotus(structure_inchikey=f"{matched_ik}-UHFFFAOYSA-N")
    form_h = make_adduct(lotus=[lotus], recipe=make_recipe(ingredients={"proton": 1}))
    form_na = make_adduct(lotus=[lotus], recipe=make_recipe(ingredients={"sodium": 1}))
    spectrum.ms1_annotations = [form_h, form_na]
    spectrum.ms2_annotations = [_ms2(matched_ik)]

    serializer = AnalysisSerializer(top_k_ms1=1)  # would cap to 1 without the coupling override
    serializer.add_analysis(analysis)
    g = serializer.graph

    ms2_uri = AnalysisURIs.ms2_annotation_uri(analysis, spectrum, spectrum.ms2_annotations[0])
    corresponding = set(g.objects(ms2_uri, ENPKG.hasCorrespondingAdduct))
    assert corresponding == {
        AnalysisURIs.chemical_adduct_uri(analysis, spectrum, form_h),
        AnalysisURIs.chemical_adduct_uri(analysis, spectrum, form_na),
    }


def test_ms2_with_no_corresponding_adduct_emits_no_ms1(
    make_analysis, make_adduct, make_lotus, make_recipe
):
    """If the MS2 compound is in none of the MS1 groups, the feature is serialized
    with no MS1 adduct at all (intended) and no dangling correspondence link."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]

    only_lotus = make_lotus(structure_inchikey="OTHERCOMPND12-UHFFFAOYSA-N")
    adduct = make_adduct(lotus=[only_lotus], recipe=make_recipe(ingredients={"proton": 1}))
    spectrum.ms1_annotations = [adduct]
    spectrum.ms2_annotations = [_ms2("MATCHEDCOMPND1")]  # absent from MS1

    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    assert set(g.subjects(RDF.type, ENPKG.AdductAnnotation)) == set()
    ms2_uri = AnalysisURIs.ms2_annotation_uri(analysis, spectrum, spectrum.ms2_annotations[0])
    assert list(g.objects(ms2_uri, ENPKG.hasCorrespondingAdduct)) == []


def test_no_ms2_keeps_full_topk_ms1(make_analysis, make_adduct, make_lotus, make_recipe):
    """A feature with no MS2 keeps its top-k MS1 adducts, uncoupled (unchanged)."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    # Distinct recipes -> distinct adduct URIs. (The key is the recipe hash *plus* the
    # candidate group's formula; here the recipes are what differ.)
    ingredients = [{"proton": 1}, {"sodium": 1}, {"potassium": 1}, {"ammonium": 1}]
    spectrum.ms1_annotations = [
        make_adduct(
            lotus=[make_lotus(structure_inchikey=f"{_short_ik(i)}-UHFFFAOYSA-N")],
            recipe=make_recipe(ingredients=ing),
        )
        for i, ing in enumerate(ingredients)
    ]
    spectrum.ms2_annotations = []

    serializer = AnalysisSerializer(top_k_ms1=2)
    serializer.add_analysis(analysis)
    g = serializer.graph

    # Exactly top_k adduct nodes, and none carry a correspondence link.
    adducts = set(g.subjects(RDF.type, ENPKG.AdductAnnotation))
    assert len(adducts) == 2
    assert list(g.subjects(ENPKG.hasCorrespondingAdduct, None)) == []


def test_same_recipe_different_compounds_are_two_adduct_nodes(
    make_analysis, make_adduct, make_lotus, make_recipe
):
    """Two molecules proposed for one feature under the *same* ionization form are two
    independent hypotheses: two nodes, each with its own masses, its own candidate
    structure and exactly one rank.

    Regression: keyed on the recipe hash alone they collapsed onto one node, which kept
    the first hypothesis' chemistry and absorbed both ranks — the second contributed a
    rank and a score and no chemistry at all. See the audit in
    docs/MS1_ADDUCT_RANKING_ISSUE.md §2.
    """
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    recipe = make_recipe(ingredients={"proton": 1})
    first = make_adduct(
        lotus=[make_lotus(structure_inchikey="AAAAAAAAAAAAAA-UHFFFAOYSA-N",
                          structure_molecular_formula="C9H8O4",
                          structure_exact_mass=180.04226)],
        recipe=recipe,
    )
    second = make_adduct(
        lotus=[make_lotus(structure_inchikey="BBBBBBBBBBBBBB-UHFFFAOYSA-N",
                          structure_molecular_formula="C10H12O2",
                          structure_exact_mass=164.08373)],
        recipe=recipe,
    )
    spectrum.ms1_annotations = [first, second]

    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    first_uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, first)
    second_uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, second)
    assert first_uri != second_uri
    assert set(g.subjects(RDF.type, ENPKG.AdductAnnotation)) == {first_uri, second_uri}

    for uri, adduct in ((first_uri, first), (second_uri, second)):
        # Exactly one rank: two would mean two hypotheses were merged into this node.
        assert len(list(g.objects(uri, ENPKG.annotationRank))) == 1
        assert g.value(uri, ENPKG.adductNeutralMass) == Literal(adduct.neutral_mass)
        assert g.value(uri, ENPKG.adductMass) == Literal(adduct.adduct_mass)
        assert set(g.objects(uri, ENPKG.hasCandidateStructure)) == {
            CompoundURIs.lotus_uri(adduct.lotus[0])
        }

    ranks = sorted(int(o) for uri in (first_uri, second_uri)
                   for o in g.objects(uri, ENPKG.annotationRank))
    assert ranks == [1, 2]
    # Splitting the node did not duplicate the globally shared recipe node.
    assert set(g.objects(first_uri, ENPKG.hasRecipe)) == set(
        g.objects(second_uri, ENPKG.hasRecipe)
    )


def test_no_annotation_node_carries_more_than_one_rank(
    make_analysis, make_adduct, make_lotus, make_recipe, make_sirius_annotation
):
    """Structural invariant across every channel (MS1 / MS2 / SIRIUS): one
    enpkg:annotationRank per annotation node.

    Two ranks on one node means two hypotheses were silently merged into it — the node
    then presents the first one's chemistry under both ranks, and `ORDER BY ?rank`
    returns it twice. This is the safety net for the deliberately-absent runtime guard
    in _add_ranked_annotations: no current producer can emit a duplicate hypothesis, so
    if that ever changes, this fails instead of an export going out wrong.
    """
    analysis = make_analysis(run_name="RUNX", n_spectra=2)
    ms1_spectrum, other_spectrum = analysis.spectra

    # Every (formula, recipe) combination — the shape that used to collide.
    groups = [("C9H8O4", 180.04226, "AAAAAAAAAAAAAA"),
              ("C10H12O2", 164.08373, "BBBBBBBBBBBBBB")]
    recipes = [make_recipe(ingredients={"proton": 1}),
               make_recipe(ingredients={"sodium": 1})]
    ms1_spectrum.ms1_annotations = [
        make_adduct(
            lotus=[make_lotus(structure_molecular_formula=formula,
                              structure_exact_mass=mass,
                              structure_inchikey=f"{ik}-UHFFFAOYSA-N")],
            recipe=recipe,
        )
        for formula, mass, ik in groups
        for recipe in recipes
    ]
    other_spectrum.ms2_annotations = [_ms2(_short_ik("mtwoa"), score=0.9),
                                      _ms2(_short_ik("mtwob"), score=0.8)]
    other_spectrum.sirius_annotations = [
        make_sirius_annotation(rank=1, inchikey_2d="SIRIUSKEYAAAAA"),
        make_sirius_annotation(rank=2, inchikey_2d="SIRIUSKEYBBBBB"),
    ]

    serializer = AnalysisSerializer(top_k_ms1=None, top_k_ms2=None)
    serializer.add_analysis(analysis)
    g = serializer.graph

    ranked = [s for s, _, _ in g.triples((None, ENPKG.annotationRank, None))]
    assert len(ranked) >= 8, "the fixture stopped producing ranked nodes on every channel"
    duplicated = sorted({str(uri) for uri in ranked if ranked.count(uri) > 1})
    assert not duplicated, f"node(s) carrying >1 enpkg:annotationRank: {duplicated}"


def test_correspondence_property_declared_once(make_analysis):
    """enpkg:hasCorrespondingAdduct is declared as an object property with
    domain/range, and re-serializing adds no duplicate triples."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    prop = ENPKG.hasCorrespondingAdduct
    assert (prop, RDF.type, OWL.ObjectProperty) in g
    assert (prop, RDFS.domain, ENPKG.SpectralAnnotation) in g
    assert (prop, RDFS.range, ENPKG.AdductAnnotation) in g

    before = len(g)
    serializer.add_analysis(analysis)
    assert len(g) == before


def test_sirius_annotations_are_serialized(make_analysis, make_sirius_annotation):
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    spectrum.sirius_annotations = [
        make_sirius_annotation(rank=1, molecular_formula="C6H9N3O3S",
                               adduct="[M+K]+", inchikey_2d="BBTZETLXNQDZKF"),
        make_sirius_annotation(rank=2, molecular_formula="C6H9N3O3S",
                               adduct="[M+K]+", inchikey_2d="QLUPQWGVSUSFPS"),
    ]
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    spectrum_uri = AnalysisURIs.spectrum_uri(analysis, spectrum)
    # SIRIUS attaches through the same emi:hasAnnotation predicate MS1/MS2 use
    # (unified, §1c) — isolate SIRIUS candidates by rdf:type, not a dedicated predicate.
    ann_links = [
        uri for uri in g.objects(spectrum_uri, EMI.hasAnnotation)
        if (uri, RDF.type, ENPKG.SiriusAnnotation) in g
    ]
    assert len(ann_links) == 2
    assert (None, ENPKG.hasSiriusAnnotation, None) not in g  # retired predicate

    for annotation in spectrum.sirius_annotations:
        uri = AnalysisURIs.sirius_annotation_uri(analysis, spectrum, annotation)
        assert (spectrum_uri, EMI.hasAnnotation, uri) in g
        assert (uri, RDF.type, ENPKG.SiriusAnnotation) in g
        assert (uri, RDF.type, EMI.StructuralAnnotation) in g
        assert (uri, CHEMROF.generalized_empirical_formula, Literal("C6H9N3O3S")) in g
        assert (uri, EMI.hasAdduct, Literal("[M+K]+")) in g
        assert (uri, ENPKG.annotationRank, Literal(annotation.rank)) in g
        # The candidate structure links to the shared InChIKey2D node.
        inchikey2d = g.value(uri, EMI.hasChemicalStructure)
        assert inchikey2d == EMI_RES[f"inchikey2d/{annotation.inchikey_2d}"]
        assert (inchikey2d, RDF.type, EMI.InChIKey2D) in g


def test_sirius_top_k_caps_annotations(make_analysis, make_sirius_annotation):
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    spectrum.sirius_annotations = [
        make_sirius_annotation(rank=rank, inchikey_2d=f"SKELETON{rank:06d}")
        for rank in range(1, 6)
    ]
    serializer = AnalysisSerializer(top_k_sirius=2)
    serializer.add_analysis(analysis)
    g = serializer.graph

    spectrum_uri = AnalysisURIs.spectrum_uri(analysis, spectrum)
    kept = [
        uri for uri in g.objects(spectrum_uri, EMI.hasAnnotation)
        if (uri, RDF.type, ENPKG.SiriusAnnotation) in g
    ]
    assert len(kept) == 2
    # The two best ranks (1, 2) survive the cap.
    ranks = sorted(int(g.value(uri, ENPKG.annotationRank)) for uri in kept)
    assert ranks == [1, 2]


def _analysis_with_network(make_spectrum, feature_ids, edges, run_name="RUNX"):
    """Build an Analysis whose molecular_network is a valid nx.Graph over feature_ids.

    Goes through the Analysis *constructor*, not model_copy: pydantic does not re-run
    validators on model_copy(update=...), so a model_copy-based fixture would happily
    accept a wrong graph and the tests would prove nothing about the invariant.
    validate_network_integrity requires the graph's nodes to be exactly
    analysis.feature_ids in the same *order*, hence seeding the nodes from that list
    before any edge is added.

    Precursor m/z varies per feature (the make_spectrum default is 200.0 for every
    spectrum) so the emitted emi:hasMassDifference is non-zero. `edges` are
    (u, v, weight) triples.
    """
    spectra = tuple(
        make_spectrum(feature_id=fid, precursor_mz=200.0 + int(fid)) for fid in feature_ids
    )
    graph = nx.Graph()
    graph.add_nodes_from(feature_ids)  # order is the load-bearing invariant
    for u, v, weight in edges:
        graph.add_edge(u, v, weight=weight)
    assert list(graph.nodes) == list(feature_ids), "edges must not introduce new nodes"
    return Analysis(
        run_name=run_name,
        spectra=spectra,
        metadata=SampleMetadata(sample_id="S1"),
        ionization_mode="pos",
        molecular_network=graph,
    )


def test_molecular_network_emits_named_lfpairs(make_spectrum):
    """Edges reify to *named* LFpair URIs keyed on the numerically-sorted pair,
    carrying cosine + mass difference — and re-adding is a no-op."""
    # Edge stated (2, 1) on purpose: orientation must not leak into the output.
    analysis = _analysis_with_network(make_spectrum, [1, 2, 3], [(2, 1, 0.83)])
    serializer = AnalysisSerializer(include_network=True)
    serializer.add_analysis(analysis)
    g = serializer.graph

    pair = AnalysisURIs.lfpair_uri(analysis, 1, 2)
    assert set(g.subjects(RDF.type, EMI.LFpair)) == {pair}
    assert str(pair).endswith("lfpair/RUNX/1_2")
    # The whole point of the named URI: nothing blank survives anywhere in the graph.
    assert not [term for triple in g for term in triple if isinstance(term, BNode)]

    first, second = analysis.spectra[0], analysis.spectra[1]
    assert g.value(pair, EMI.hasFirstMember) == AnalysisURIs.spectrum_uri(analysis, first)
    assert g.value(pair, EMI.hasSecondMember) == AnalysisURIs.spectrum_uri(analysis, second)
    assert (pair, EMI.hasCosine, Literal(0.83)) in g
    assert (
        pair,
        EMI.hasMassDifference,
        Literal(abs(second.precursor_mz - first.precursor_mz)),
    ) in g

    # Was NOT true with blank nodes — every re-add duplicated the edge.
    before = len(g)
    serializer.add_analysis(analysis)
    assert len(g) == before


def test_lfpair_is_orientation_independent(make_spectrum):
    """The same edge stated (1, 2) and (2, 1) produces the same triples."""
    forward = _analysis_with_network(make_spectrum, [1, 2], [(1, 2, 0.9)])
    reverse = _analysis_with_network(make_spectrum, [1, 2], [(2, 1, 0.9)])
    a = AnalysisSerializer(include_network=True)
    b = AnalysisSerializer(include_network=True)
    a.add_analysis(forward)
    b.add_analysis(reverse)
    # Comparable as sets only because there are no blank nodes left to re-mint.
    assert set(a.graph) == set(b.graph)


def test_fbmn_components_are_serialized(make_spectrum):
    """Connected components become emi:FBMNComponent nodes keyed on the minimum
    member feature id; isolated features get none."""
    analysis = _analysis_with_network(
        make_spectrum,
        [1, 2, 3, 4, 5, 6],
        [(1, 2, 0.9), (2, 3, 0.8), (4, 5, 0.75)],  # {1,2,3}, {4,5}, 6 isolated
    )
    serializer = AnalysisSerializer()  # components on by default, edges off
    serializer.add_analysis(analysis)
    g = serializer.graph

    big = AnalysisURIs.fbmn_component_uri(analysis, 1)
    small = AnalysisURIs.fbmn_component_uri(analysis, 4)
    featureset = AnalysisURIs.featureset_uri(analysis)

    assert set(g.subjects(RDF.type, EMI.FBMNComponent)) == {big, small}
    assert set(g.objects(featureset, ENPKG.hasNetworkComponent)) == {big, small}
    assert (big, ENPKG.componentSize, Literal(3)) in g
    assert (small, ENPKG.componentSize, Literal(2)) in g
    assert set(g.objects(big, ENPKG.hasComponentMember)) == {
        AnalysisURIs.spectrum_uri(analysis, s) for s in analysis.spectra[:3]
    }
    # EMI's property runs feature -> component (its rdfs:domain is LCMSFeature)...
    for spectrum in analysis.spectra[:3]:
        assert (AnalysisURIs.spectrum_uri(analysis, spectrum), EMI.hasFBMNComponent, big) in g
    # ...and is never used off the feature set, which would entail it is a feature.
    assert list(g.objects(featureset, EMI.hasFBMNComponent)) == []

    isolated = AnalysisURIs.spectrum_uri(analysis, analysis.spectra[5])
    assert list(g.objects(isolated, EMI.hasFBMNComponent)) == []
    assert (AnalysisURIs.fbmn_component_uri(analysis, 6), RDF.type, EMI.FBMNComponent) not in g
    # Components are gated independently of the edges.
    assert set(g.subjects(RDF.type, EMI.LFpair)) == set()

    before = len(g)
    serializer.add_analysis(analysis)
    assert len(g) == before


def test_fbmn_component_key_is_numeric_not_lexicographic(make_spectrum):
    """Network node ids are strings in production, so the component key must be the
    numeric minimum — min() on the raw strings would pick '10' over '9'."""
    analysis = _analysis_with_network(
        make_spectrum, ["9", "10", "11"], [("9", "10", 0.9), ("10", "11", 0.9)]
    )
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    component = AnalysisURIs.fbmn_component_uri(analysis, 9)
    assert set(serializer.graph.subjects(RDF.type, EMI.FBMNComponent)) == {component}
    assert str(component).endswith("fbmncomponent/RUNX/9")


def test_fbmn_components_can_be_disabled(make_spectrum):
    analysis = _analysis_with_network(make_spectrum, [1, 2], [(1, 2, 0.9)])
    serializer = AnalysisSerializer(include_fbmn_components=False)
    serializer.add_analysis(analysis)
    g = serializer.graph

    # No component *instances* (the term declarations themselves are unaffected).
    assert set(g.subjects(RDF.type, EMI.FBMNComponent)) == set()
    assert list(g.objects(AnalysisURIs.featureset_uri(analysis), ENPKG.hasNetworkComponent)) == []


def test_network_layers_are_no_ops_without_a_network(make_analysis):
    analysis = make_analysis(run_name="RUNX", n_spectra=2)  # molecular_network is None
    serializer = AnalysisSerializer(include_network=True, include_fbmn_components=True)
    serializer.add_analysis(analysis)

    assert set(serializer.graph.subjects(RDF.type, EMI.LFpair)) == set()
    assert set(serializer.graph.subjects(RDF.type, EMI.FBMNComponent)) == set()


def test_fbmn_component_terms_declared_once(make_analysis):
    """The minted component terms are declared with domain/range, and re-serializing
    adds no duplicate triples."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    assert (ENPKG.hasNetworkComponent, RDF.type, OWL.ObjectProperty) in g
    assert (ENPKG.hasNetworkComponent, RDFS.domain, EMI.LCMSFeatureSet) in g
    assert (ENPKG.hasNetworkComponent, RDFS.range, EMI.FBMNComponent) in g
    assert (ENPKG.hasComponentMember, RDFS.domain, EMI.FBMNComponent) in g
    assert (ENPKG.hasComponentMember, RDFS.range, EMI.LCMSFeature) in g
    assert (ENPKG.componentSize, RDF.type, OWL.DatatypeProperty) in g

    before = len(g)
    serializer.add_analysis(analysis)
    assert len(g) == before


# --------------------------------------------------------------- CANOPUS classification
def _canopus_graph(make_analysis, make_canopus_classification, **kwargs):
    """Serialize a one-spectrum analysis carrying a CANOPUS classification."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    analysis.spectra[0].canopus_classification = make_canopus_classification(**kwargs)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    return analysis, serializer.graph


def test_canopus_emits_a_chemical_taxon_annotation(make_analysis, make_canopus_classification):
    analysis, g = _canopus_graph(make_analysis, make_canopus_classification)
    uri = AnalysisURIs.canopus_annotation_uri(analysis, analysis.spectra[0])

    assert (uri, RDF.type, EMI.ChemicalTaxonAnnotation) in g
    assert (AnalysisURIs.spectrum_uri(analysis, analysis.spectra[0]), EMI.hasAnnotation, uri) in g
    assert (uri, CHEMROF.generalized_empirical_formula, Literal("C18H24O13")) in g
    assert (uri, EMI.hasAdduct, Literal("[M+H]+")) in g
    assert (uri, EMI.hasPathway, NPC["TERPENOIDS"]) in g
    assert (uri, EMI.hasSuperClass, NPC["MONOTERPENOIDS"]) in g
    assert (uri, EMI.hasClass, NPC["IRIDOIDS_MONOTERPENOIDS"]) in g
    assert (uri, EMI.hasPathwayProbability, Literal(0.982, datatype=XSD.double)) in g


def test_canopus_node_is_not_also_a_structural_annotation(make_analysis, make_canopus_classification,
                                                          make_sirius_annotation):
    """emi:ChemicalTaxonAnnotation is owl:disjointWith emi:StructuralAnnotation.

    Hanging the class predicates on the spectrum's SIRIUS annotation node instead of
    minting a separate one would make the graph inconsistent under any reasoner --
    invalidating every entailment over it, not merely the CANOPUS part.
    """
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    spectrum.canopus_classification = make_canopus_classification()
    spectrum.sirius_annotations = [make_sirius_annotation(rank=1)]
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    g = serializer.graph

    canopus_uri = AnalysisURIs.canopus_annotation_uri(analysis, spectrum)
    sirius_uri = AnalysisURIs.sirius_annotation_uri(analysis, spectrum, spectrum.sirius_annotations[0])

    assert canopus_uri != sirius_uri
    assert (canopus_uri, RDF.type, EMI.StructuralAnnotation) not in g
    assert (sirius_uri, RDF.type, EMI.ChemicalTaxonAnnotation) not in g
    # no node anywhere carries both types
    both = set(g.subjects(RDF.type, EMI.ChemicalTaxonAnnotation)) & set(
        g.subjects(RDF.type, EMI.StructuralAnnotation)
    )
    assert both == set()
    # both still hang off the same feature via the shared predicate
    spectrum_uri = AnalysisURIs.spectrum_uri(analysis, spectrum)
    assert {canopus_uri, sirius_uri} <= set(g.objects(spectrum_uri, EMI.hasAnnotation))


def test_canopus_inlines_the_npc_terms_it_uses(make_analysis, make_canopus_classification):
    """Without this the graph carries bare IRIs and class queries silently return nothing.

    _declare_vocabulary inlines enpkg.ttl only; enpkg.ttl declares owl:imports emi:, but an
    import is a pointer, not content.
    """
    _, g = _canopus_graph(make_analysis, make_canopus_classification)

    assert (NPC["IRIDOIDS_MONOTERPENOIDS"], RDF.type, NPC["Class"]) in g
    assert (NPC["IRIDOIDS_MONOTERPENOIDS"], RDFS.label, Literal("Iridoids monoterpenoids")) in g
    # the skos:broader chain must reach the pathway, or roll-up queries stop short
    assert (NPC["IRIDOIDS_MONOTERPENOIDS"], SKOS.broader, NPC["MONOTERPENOIDS"]) in g
    assert (NPC["MONOTERPENOIDS"], SKOS.broader, NPC["TERPENOIDS"]) in g
    assert (NPC["TERPENOIDS"], RDF.type, NPC["Pathway"]) in g


# rdflib's SPARQL parser trips pyparsing deprecation warnings on every parse (hundreds of
# them, from library internals we do not control). Scoped here so the one test that runs
# a real query does not drown the suite output.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_canopus_hierarchy_roll_up_is_queryable(make_analysis, make_canopus_classification):
    """The query the inlining exists to make possible."""
    _, g = _canopus_graph(make_analysis, make_canopus_classification)
    rows = list(g.query(
        """SELECT (COUNT(DISTINCT ?f) AS ?n) WHERE {
             ?f emi:hasAnnotation ?a . ?a emi:hasClass ?c . ?c skos:broader+ npc:TERPENOIDS }""",
        initNs={"emi": EMI, "npc": NPC, "skos": SKOS},
    ))
    assert int(rows[0][0]) == 1


def test_canopus_keeps_a_zero_probability(make_analysis, make_canopus_classification):
    """0.0 is a probability CANOPUS really emits, and it is falsy.

    Dropping it would leave the graph asserting a rank with no confidence attached, which
    is worse than emitting neither.
    """
    analysis, g = _canopus_graph(
        make_analysis, make_canopus_classification,
        superclass=("γ-lactam-β-lactones", 0.0),
    )
    uri = AnalysisURIs.canopus_annotation_uri(analysis, analysis.spectra[0])
    assert (uri, EMI.hasSuperClassProbability, Literal(0.0, datatype=XSD.double)) in g


def test_canopus_is_absent_when_unclassified(make_analysis):
    """A feature CANOPUS could not classify contributes no node at all."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    assert set(serializer.graph.subjects(RDF.type, EMI.ChemicalTaxonAnnotation)) == set()


def test_canopus_serialization_is_idempotent(make_analysis, make_canopus_classification):
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    analysis.spectra[0].canopus_classification = make_canopus_classification()
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    before = len(serializer.graph)
    serializer.add_analysis(analysis)
    assert len(serializer.graph) == before

