import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig, SiriusParams
from enpkg.monolith.enhancers.sirius_enhancer import SiriusEnhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
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
def network_and_taxa_enhanced_analysis(analysis, taxa_enhancer, network_enhancer):
    """Chain the taxa and network enhancers over the fixture analysis.

    Both follow the uniform enhancer contract — take an Analysis, return an
    enriched copy — so the network enhancer receives the taxa-enriched analysis
    and attaches the molecular network to it.
    """
    return network_enhancer.enhance(taxa_enhancer.enhance(analysis))

@pytest.fixture
def sirius_config() -> SiriusEnhancerConfig:
    """
    Fixture providing a mock configuration for SiriusEnhancer.

    Returns:
        SiriusEnhancerConfig: Configuration object containing nested paths
        and general parameters needed to initialize the enhancer.
    """
    return SiriusEnhancerConfig(
        general_params=GeneralParams(ionization_mode="pos"),
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

        # Check the actual Sirius run call. The argv is asserted structurally
        # rather than as one literal list: the project directory carries a run
        # timestamp and --input is resolved to an absolute path, so both differ
        # between runs and between platforms.
        run_args, _ = mock_run.call_args_list[1]
        argv = run_args[0]
        params = sirius_config.sirius_params

        db_list = (
            "public_spectra_2506,METACYC,BloodExposome,CHEBI,COCONUT,FooDB,"
            "GNPS,HMDB,HSDB,KEGG,KNAPSACK,LOTUS,LIPIDMAPS,MACONDA,MESH,MiMeDB,NORMAN,PLANTCYC,"
            "PUBCHEMANNOTATIONBIO,PUBCHEMANNOTATIONDRUG,PUBCHEMANNOTATIONFOOD,"
            "PUBCHEMANNOTATIONSAFETYANDTOXIC,SUPERNATURAL,TeroMol,YMDB"
        )
        sample_stem = network_and_taxa_enhanced_analysis.metadata.sample_filename_pos.split(".")[0]

        assert argv[0] == params.path_to_sirius
        assert argv[1] == "--input"
        assert argv[2] == str(Path(params.path_to_input_spectra).resolve())

        # SIRIUS 6 stores each project as a single .sirius file inside a
        # timestamped directory under the configured output directory.
        assert argv[3] == "-o"
        project_file = Path(argv[4])
        assert project_file.name == f"{sample_stem}.sirius"
        assert project_file.parent.parent == Path(params.output_directory).resolve()

        # Configuration options are passed through the `config` subcommand.
        assert argv[5] == "config"
        assert set(argv[6:]) >= {
            "--AlgorithmProfile=orbitrap",
            f"--MS2MassDeviation.allowedMassDeviation={params.ms2_mass_deviation}ppm",
            f"--SpectralSearchDB={db_list}",
            "--AdductSettings.fallback=[[M+H]+,[M+Na]+,[M+K]+]",
            f"--NumberOfCandidates={params.top_k_sirius}",
            "--FormulaSettings.enforced=H,C,N,O,P",
            f"--IdentitySearchSettings.precursorDeviation={params.identity_search_precursor_deviation}ppm",
            "--FormulaSearchSettings.performBottomUpAboveMz=0",
            "--ExpansiveSearchConfidenceMode.confidenceScoreSimilarityMode=EXACT",
            "--FormulaSearchDB=",
            f"--StructureSearchDB={db_list}",
            "--SpectralSearchLog=0",
        }

        # Tool subcommands run after the configuration block, in this order.
        tool_positions = [
            argv.index(tool)
            for tool in ("spectra-search", "formulas", "fingerprints",
                         "classes", "structures", "write-summaries")
        ]
        assert tool_positions == sorted(tool_positions)

        assert argv[-3:] == [
            "--output",
            params.output_directory + "/summaries/",
            f"--top-k-summary={params.top_k_sirius}",
        ]

