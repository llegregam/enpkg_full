"""Test suite for the Network Enhancer."""
import logging
from time import time
from typing import Any
import networkx as nx
import pytest

from enpkg.monolith.enhancers.network_enhancer import NetworkEnhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.tests.test_enhancers.conftest import TEST_DATA_DIR


@pytest.fixture(scope="class")
def analysis():
    """Load a test analysis."""
    return AnalysisLoader.from_files(
        path_to_spectra=TEST_DATA_DIR / "enpkg_toy_dataset/msdata/processed/VGF151_E05_pos.mgf",
        path_to_metadata=TEST_DATA_DIR / "enpkg_toy_dataset/metadata/metadata.tsv",
        path_to_quant_table=TEST_DATA_DIR / "enpkg_toy_dataset/msdata/processed/VGF151_E05_pos_quant.csv",
        ionization_mode="pos",
    )


class TestNetworkEnhancer:
    """Test class for NetworkEnhancer."""

    def test_initialization(self, network_enhancer: NetworkEnhancer) -> None:
        """Test that NetworkEnhancer correctly initializes.

        Args:
            network_enhancer: The network enhancer instance correctly initialized via fixtures.
        """
        assert network_enhancer is not None, "NetworkEnhancer should not be None."
        assert network_enhancer.name() == "Network Enhancer", "NetworkEnhancer name property should match."
        assert network_enhancer.configuration is not None, "NetworkEnhancer configuration should be assigned."

    def test_enhance_spectra(self, network_enhancer: NetworkEnhancer, analysis: Any, logger: logging.Logger) -> None:
        """Test the molecular network generation.

        Args:
            network_enhancer: The fully populated NetworkEnhancer instance.
            analysis: Main analysis dataset loaded from project test directory.
            logger: A contextual logger.
        """
        assert analysis.spectra is not None, "Analysis should have spectra to pass to network enhancer."
        assert analysis.feature_ids is not None, "Analysis should have feature_ids."

        original_spectra_count = len(analysis.spectra)
        feature_count = len(analysis.feature_ids)

        assert original_spectra_count > 0, "No spectra loaded for testing."

        start = time()
        graph = network_enhancer.enhance(analysis)
        logger.info(f"Generated network with {len(graph.nodes)} nodes and {len(graph.edges)} edges in {time() - start:.2f} seconds")

        assert isinstance(graph, nx.Graph), "Enhance should return a networkx Graph."
        assert len(graph.nodes) == feature_count, "The number of nodes should match the number of feature_ids."

