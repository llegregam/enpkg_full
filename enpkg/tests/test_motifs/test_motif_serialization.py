"""The Mass2Motif layer of the RDF serializer."""

from pathlib import Path

import pytest
from rdflib import BNode, Graph, Literal
from rdflib.namespace import RDF, RDFS, XSD

from enpkg.monolith.data.mass2motif import (
    Mass2Motif,
    Mass2MotifSet,
    MotifCandidate,
    MotifMembership,
)
from enpkg.monolith.rdf import AnalysisSerializer
from enpkg.monolith.rdf.namespaces import DCTERMS, EMI, ENPKG, INCHIKEY, PROV
from enpkg.monolith.rdf.uris import AnalysisURIs, MotifURIs

VOCABULARY = Path(__file__).resolve().parents[3] / "docs" / "vocab" / "enpkg.ttl"

MOTIF_SET = Mass2MotifSet(
    model_id="0123456789abcdef",
    ionization_mode="pos",
    software_version="2.0.1",
    motifs=(
        Mass2Motif(
            index=0,
            fragments=((70.04, 0.2),),
            losses=((162.05, 0.1),),
            candidates=(
                MotifCandidate("CC", "OTMSDBZUPAUEDD-UHFFFAOYSA-N", 0.81),
                MotifCandidate("not a smiles", None, 0.7),
            ),
        ),
        Mass2Motif(index=1, fragments=((105.07, 0.3),), losses=()),
    ),
)


@pytest.fixture
def analysis(make_analysis):
    analysis = make_analysis(n_spectra=2)
    analysis.spectra[0].motif_memberships = [MotifMembership(0, 0.5, 0.0)]
    return analysis.model_copy(update={"motif_set": MOTIF_SET})


def _graph(analysis):
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    return serializer.graph


def test_a_membership_is_an_annotation_of_the_feature(analysis):
    g = _graph(analysis)
    spectrum = analysis.spectra[0]
    annotation = AnalysisURIs.motif_annotation_uri(analysis, spectrum, MOTIF_SET, 0)
    assert (AnalysisURIs.spectrum_uri(analysis, spectrum), EMI.hasAnnotation, annotation) in g
    assert (annotation, RDF.type, ENPKG.Mass2MotifAnnotation) in g
    assert (annotation, ENPKG.motifMembershipProbability, Literal(0.5, datatype=XSD.double)) in g
    assert (annotation, ENPKG.motifOverlapScore, Literal(0.0, datatype=XSD.double)) in g
    assert (annotation, ENPKG.hasMass2Motif, MotifURIs.motif_uri(MOTIF_SET, 0)) in g


def test_the_motif_carries_its_bins_model_and_candidates(analysis):
    g = _graph(analysis)
    motif = MotifURIs.motif_uri(MOTIF_SET, 0)
    model = MotifURIs.model_uri(MOTIF_SET)
    assert (motif, RDF.type, ENPKG.Mass2Motif) in g
    assert (motif, DCTERMS.identifier, Literal("motif_0")) in g
    assert (motif, PROV.wasGeneratedBy, model) in g
    assert (model, RDF.type, EMI.ComputationalAnalysis) in g
    assert (model, DCTERMS.identifier, Literal("0123456789abcdef")) in g

    fragment = MotifURIs.motif_bin_uri(motif, "fragment", 70.04)
    loss = MotifURIs.motif_bin_uri(motif, "loss", 162.05)
    assert str(fragment).endswith("/mass2motif/0123456789abcdef/0/fragment/70.04")
    assert (motif, ENPKG.hasMotifFragment, fragment) in g
    assert (fragment, RDF.type, ENPKG.Mass2MotifBin) in g
    assert (fragment, ENPKG.motifFragmentMz, Literal(70.04, datatype=XSD.double)) in g
    assert (fragment, ENPKG.motifBinProbability, Literal(0.2, datatype=XSD.double)) in g
    assert (motif, ENPKG.hasMotifLoss, loss) in g
    assert (loss, RDF.type, ENPKG.Mass2MotifBin) in g
    assert (loss, ENPKG.motifLossMass, Literal(162.05, datatype=XSD.double)) in g
    assert (loss, ENPKG.motifBinProbability, Literal(0.1, datatype=XSD.double)) in g


def test_candidates_are_the_shared_inchikey_structure_nodes(analysis):
    g = _graph(analysis)
    motif = MotifURIs.motif_uri(MOTIF_SET, 0)
    candidates = list(g.objects(motif, ENPKG.hasMotifCandidate))
    assert candidates == [INCHIKEY["OTMSDBZUPAUEDD-UHFFFAOYSA-N"]]
    assert (candidates[0], RDF.type, EMI.ChemicalStructure) in g
    assert (candidates[0], EMI.hasSMILES, Literal("CC")) in g


def test_motif_probabilities_are_emi_probabilities_under_rdfs_inference():
    vocabulary = Graph().parse(VOCABULARY, format="turtle")
    for prop in (ENPKG.motifMembershipProbability, ENPKG.motifBinProbability):
        assert (prop, RDFS.subPropertyOf, EMI.hasProbability) in vocabulary


def test_only_motifs_the_analysis_references_are_emitted(analysis):
    g = _graph(analysis)
    assert set(g.subjects(RDF.type, ENPKG.Mass2Motif)) == {MotifURIs.motif_uri(MOTIF_SET, 0)}


def test_the_layer_mints_no_blank_nodes(analysis):
    g = _graph(analysis)
    assert not [t for t in g if any(isinstance(term, BNode) for term in t)]


def test_serializing_twice_adds_nothing(analysis):
    serializer = AnalysisSerializer()
    serializer.add_analysis(analysis)
    first = len(serializer.graph)
    serializer.add_analysis(analysis)
    assert len(serializer.graph) == first


def test_without_a_motif_set_nothing_is_emitted(make_analysis):
    analysis = make_analysis(n_spectra=1)
    analysis.spectra[0].motif_memberships = [MotifMembership(0, 0.5, 0.2)]
    g = _graph(analysis)
    assert not list(g.subjects(RDF.type, ENPKG.Mass2MotifAnnotation))
    assert not list(g.subjects(RDF.type, ENPKG.Mass2Motif))
