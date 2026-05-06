import logging
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from enpkg.monolith.enhancers.sirius_enhancer import SiriusEnhancer
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig, SiriusParams
from enpkg.monolith.configuration.config import GeneralParams
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


@pytest.fixture(scope="class")
def network_and_taxa_enhanced_analysis(analysis, taxa_enhancer, network_enhancer):
    genus, species = analysis.genus_and_species
    new_matches = taxa_enhancer.enhance(genus, species)
    analysis.ott_matches += new_matches
    molecular_network = network_enhancer.enhance(analysis)
    return analysis.model_copy(update={"molecular_network": molecular_network})

@pytest.fixture
def sirius_config() -> SiriusEnhancerConfig:
    """
    Fixture providing a mock configuration for SiriusEnhancer.
    
    Returns:
        SiriusEnhancerConfig: Configuration object containing nested paths 
        and general parameters needed to initialize the enhancer.
    """
    return SiriusEnhancerConfig(
        general_params=GeneralParams(polarity="pos"),
        sirius_params=SiriusParams(
            path_to_sirius="/mock/path/to/sirius",
            output_directory="/mock/out",
            path_to_input_spectra="/mock/input.mgf"
        )
    )

class TestSiriusEnhancer:
    """Unit tests for the SiriusEnhancer, focusing on initialization and subprocess orchestration."""

    def test_sirius_enhancer_initialization(
        self, 
        sirius_config: SiriusEnhancerConfig, 
        logger: logging.Logger
    ) -> None:
        """
        Verify that SiriusEnhancer initializes correctly.
        
        Args:
            sirius_config (SiriusEnhancerConfig): The mocked configuration.
            logger (logging.Logger): An injected conceptual logger.
            
        Asserts:
            - The enhancer name returns 'Sirius Enhancer'.
            - The configuration property is preserved.
        """
        enhancer = SiriusEnhancer(config=sirius_config, logger=logger)
        assert enhancer.name() == "Sirius Enhancer"
        assert enhancer.config == sirius_config

    @patch('subprocess.run')
    def test_sirius_enhancer_enhance(
        self, 
        mock_run: MagicMock, 
        sirius_config: SiriusEnhancerConfig, 
        logger: logging.Logger, 
        network_and_taxa_enhanced_analysis: Any
    ) -> None:
        """
        Test the execution of the `enhance` method via subprocess mocking.
        
        Args:
            mock_run (MagicMock): The mock object for subprocess.run.
            sirius_config (SiriusEnhancerConfig): The mocked configuration.
            logger (logging.Logger): The logger instance.
            network_and_taxa_enhanced_analysis (Any): Mock analysis object needed for sample filename parsing.
            
        Asserts:
            - enhance() returns the same analysis object (side-effect is writing to disk).
            - subprocess.run is called exactly twice (once for login, once for calculation).
            - Command line arguments passed to the subprocess match expected formats.
        """
        enhancer = SiriusEnhancer(config=sirius_config, logger=logger)
        
        # Test the enhance method
        res = enhancer.enhance(network_and_taxa_enhanced_analysis)
        
        # Ensure it returns the same analysis (Sirius output is written to disk)
        assert res is network_and_taxa_enhanced_analysis
        
        # verify subprocess.run was called twice (login, and then run)
        assert mock_run.call_count == 2
        
        # Check login call
        login_args, login_kwargs = mock_run.call_args_list[0]
        assert login_args[0] == [
            "/mock/path/to/sirius", "login", 
            "--user-env", "SIRIUS_USERNAME", 
            "--password-env", "SIRIUS_PASSWORD", "--show"
        ]
        
        # Check actual Sirius run call
        run_args, _ = mock_run.call_args_list[1]
        
        expected_run_args = [
            "/mock/path/to/sirius",
            "--input", "/mock/input.mgf",
            "-o", f"/mock/out/{network_and_taxa_enhanced_analysis.metadata.sample_filename_pos.split('.')[0]}",
            "formula",
            "fingerprint",
            "canopus",
            "structure",
            "--database",
            "pubchem",
            "write-summaries",
            "--zip-output"
        ]
        
        assert run_args[0] == expected_run_args

