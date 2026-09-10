
import pytest

from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.enhancers.weights_enhancer import WeightsEnhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.tests.test_enhancers.conftest import FIXTURE_DATASET


@pytest.fixture(scope="class")
def analysis():
    """Load a test analysis."""
    return AnalysisLoader.from_files(
        path_to_spectra=FIXTURE_DATASET / "msdata/processed/arnica_0_125_pos_merged.mgf",
        path_to_metadata=FIXTURE_DATASET / "metadata/metadata.tsv",
        path_to_quant_table=FIXTURE_DATASET / "msdata/processed/arnica_0_125_pos_merged_quant.csv",
        ionization_mode="pos",
    )

@pytest.fixture(scope="class")
def lotus_store(ms_enhancer_config, logger) -> LotusStore:
    return LotusStore(
        duckdb_path=ms_enhancer_config.duckdb_path,
        logger=logger,
    )


@pytest.fixture(scope="class")
def taxa_enhanced_analysis(analysis, taxa_enhancer, network_enhancer) -> Analysis:
    """Chain the taxa and network enhancers over the fixture analysis.

    Both follow the uniform enhancer contract — take an Analysis, return an
    enriched copy — so the network enhancer receives the taxa-enriched analysis
    and attaches the molecular network to it.
    """
    return network_enhancer.enhance(taxa_enhancer.enhance(analysis))

@pytest.fixture(scope="class")
def weights_enhancer(reweighting_config, logger, lotus_store) -> WeightsEnhancer:
    """Fixture to initialize the WeightsEnhancer with the provided configuration, logger, and lotus_store."""
    return WeightsEnhancer(configuration=reweighting_config, logger=logger, lotus_store=lotus_store)


# Run test with following command:
# pytest -v -s enpkg/tests/test_weighting_enhancer.py

class TestWeightsEnhancer:

    def test_weights_enhancer(self, weights_enhancer, taxa_enhanced_analysis):
        """Test that the WeightsEnhancer initializes correctly with a given configuration
        and logger, and successfully weights the analysis spectral annotations."""

        # Verify basic initialization properties
        assert weights_enhancer.name() == "Weights Enhancer"
        assert isinstance(weights_enhancer.configuration, ReweightingConfig)
        assert isinstance(weights_enhancer.lotus_store, LotusStore)

        # Verify that enhance updates the analysis object in place and appropriately sets features
        final_enhanced_analysis = weights_enhancer.enhance(taxa_enhanced_analysis)

        assert final_enhanced_analysis is not None

        # Verify that scores have been propagated to the spectra
        at_least_one_spectrum_has_scores = False
        for spectrum in final_enhanced_analysis.spectra:
            if hasattr(spectrum, 'ms1_pathway_scores') and spectrum.ms1_pathway_scores is not None:
                at_least_one_spectrum_has_scores = True
                assert hasattr(spectrum, 'ms1_superclass_scores')
                assert hasattr(spectrum, 'ms1_class_scores')
                break

        assert at_least_one_spectrum_has_scores, "Expected MS1 classification scores to be computed and added to at least one spectrum"
