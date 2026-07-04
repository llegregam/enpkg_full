"""Smoke tests for the RDF serializer (spine + idempotency + round-trip)."""

from rdflib import Graph

from enpkg.monolith.rdf import AnalysisSerializer, serialize_to_turtle
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
