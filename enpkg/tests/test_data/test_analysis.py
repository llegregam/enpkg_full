"""Unit tests for the Analysis data model (properties + network validator)."""

import networkx as nx
import pytest

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.exceptions import EnrichmentError


def test_number_of_spectra_and_feature_ids(make_analysis):
    analysis = make_analysis(n_spectra=3)
    assert analysis.number_of_spectra == 3
    assert analysis.feature_ids == [1, 2, 3]


def test_genus_and_species_normalizes(make_analysis):
    assert make_analysis(source_taxon="Artemisia annua").genus_and_species == (
        "artemisia",
        "annua",
    )
    assert make_analysis(source_taxon="Genus sp. Foo").genus_and_species == ("genus", "foo")


def test_genus_and_species_requires_taxon(make_analysis):
    with pytest.raises(EnrichmentError):
        _ = make_analysis(source_taxon=None).genus_and_species


def test_genus_and_species_requires_two_tokens(make_analysis):
    with pytest.raises(EnrichmentError, match="genus and species"):
        _ = make_analysis(source_taxon="Artemisia").genus_and_species


def test_best_ott_match_is_none_when_empty(make_analysis):
    assert make_analysis().best_ott_matches is None


def _analysis_with_network(make_spectrum, nodes):
    spectra = (make_spectrum(feature_id=1), make_spectrum(feature_id=2))
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    return Analysis(
        run_name="R",
        spectra=spectra,
        metadata=SampleMetadata(sample_id="S1"),
        ionization_mode="pos",
        molecular_network=graph,
    )


def test_network_validator_accepts_aligned_graph(make_spectrum):
    analysis = _analysis_with_network(make_spectrum, [1, 2])
    assert list(analysis.molecular_network.nodes) == [1, 2]


def test_network_validator_rejects_mismatched_nodes(make_spectrum):
    with pytest.raises(ValueError, match="do not match|nodes"):
        _analysis_with_network(make_spectrum, [1, 999])


def test_network_validator_rejects_wrong_order(make_spectrum):
    with pytest.raises(ValueError, match="order"):
        _analysis_with_network(make_spectrum, [2, 1])
