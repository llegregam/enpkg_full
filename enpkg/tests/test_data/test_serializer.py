"""Smoke tests for the RDF serializer (spine + idempotency + round-trip)."""

from rdflib import Graph, Literal
from rdflib.namespace import RDF

from enpkg.monolith.rdf import AnalysisSerializer, serialize_to_turtle
from enpkg.monolith.rdf.namespaces import CHEMROF, EMI, EMI_RES, ENPKG
from enpkg.monolith.rdf.uris import AnalysisURIs


def test_serialize_emits_the_analysis_node(make_analysis):
    analysis = make_analysis(run_name="RUNX", n_spectra=2)
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)

    assert len(serializer.graph) > 0
    assert AnalysisURIs.analysis_uri(analysis) in set(serializer.graph.subjects())


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
    # One hasSiriusAnnotation link per candidate row.
    ann_links = list(g.objects(spectrum_uri, ENPKG.hasSiriusAnnotation))
    assert len(ann_links) == 2

    for annotation in spectrum.sirius_annotations:
        uri = AnalysisURIs.sirius_annotation_uri(analysis, spectrum, annotation)
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
    kept = list(g.objects(spectrum_uri, ENPKG.hasSiriusAnnotation))
    assert len(kept) == 2
    # The two best ranks (1, 2) survive the cap.
    ranks = sorted(int(g.value(uri, ENPKG.annotationRank)) for uri in kept)
    assert ranks == [1, 2]
