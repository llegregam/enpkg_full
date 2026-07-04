"""Submodule for creating a similarity network for the analysis."""

import networkx as nx
from matchms import calculate_scores
from matchms.networking import SimilarityNetwork
from matchms.similarity import ModifiedCosine

from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.enhancers.enhancer import Enhancer


class NetworkEnhancer(Enhancer):
    """Enhancer that creates a Spectral Network from spectra of the analysis."""

    def __init__(self, configuration: NetworkEnhancerConfig):
        """Initializes the enhancer."""
        assert isinstance(configuration, NetworkEnhancerConfig)

        self.configuration = configuration

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Network Enhancer"

    def enhance(self, analysis: Analysis) -> Analysis:
        """Return the analysis with its molecular similarity network attached."""

        similarities = calculate_scores(
            analysis.spectra,
            analysis.spectra,
            similarity_function=ModifiedCosine(
                tolerance=self.configuration.mn_msms_mz_tol
            ),
            is_symmetric=True,
        )

        ms_network = SimilarityNetwork(
            identifier_key="scans",
            score_cutoff=self.configuration.mn_score_cutoff,
            top_n=self.configuration.mn_top_n,
            max_links=self.configuration.mn_max_links,
            link_method="mutual",
        )
        ms_network.create_network(similarities, score_name="ModifiedCosine_score")

        # Rebuild the graph with nodes in feature-id order so it lines up with
        # analysis.spectra (the Analysis.validate_network_integrity invariant).
        corrected_graph = nx.Graph()
        corrected_graph.add_nodes_from(analysis.feature_ids)
        corrected_graph.add_edges_from(ms_network.graph.edges(data=True))

        return analysis.model_copy(update={"molecular_network": corrected_graph})


