"""Test suite for the MS2 Enhancer."""

import logging
from time import time
from typing import Any

import pytest

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.enhancers.ms2_enhancer import Ms2Enhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.database_loader import DBLoader
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
def db_loader(ms_enhancer_config: MSEnhancerConfig, logger: logging.Logger) -> DBLoader:
    return DBLoader(configuration=ms_enhancer_config, logger=logger)


@pytest.fixture(scope="class")
def lotus_store(ms_enhancer_config: MSEnhancerConfig, logger: logging.Logger) -> LotusStore:
    return LotusStore(
        duckdb_path=ms_enhancer_config.downloader_params.duckdb_path,
        logger=logger,
    )


@pytest.fixture(scope="class")
def ms2_enhancer(
    ms_enhancer_config: MSEnhancerConfig,
    logger: logging.Logger,
    db_loader: DBLoader,
    lotus_store: LotusStore,
) -> Ms2Enhancer:
    return Ms2Enhancer(
        configuration=ms_enhancer_config, logger=logger,
        db_loader=db_loader, lotus_store=lotus_store,
    )


class TestMs2Enhancer:
    """Test class for Ms2Enhancer."""

    def test_initialization(self, ms2_enhancer: Ms2Enhancer, logger: logging.Logger) -> None:
        """Test that Ms2Enhancer correctly initializes and defers Lotus loading to first enhance().

        Args:
            ms2_enhancer: The MS2 enhancer instance correctly initialized via fixtures.
            logger: A logger instance.
        """
        assert ms2_enhancer is not None, "Ms2Enhancer should not be None."
        # Lotus objects are now built lazily on first enhance() call, not at __init__.
        assert ms2_enhancer.lotus_objects is None, "Ms2Enhancer lotus_objects should be None before enhance()."
        assert ms2_enhancer.name() == "MS2 Enhancer", "Ms2Enhancer name property should match."

    def test_enhance_spectra(self, ms2_enhancer: Ms2Enhancer, analysis: Any, logger: logging.Logger) -> None:
        """Test the MS2 spectrum enrichment.

        Args:
            ms2_enhancer: The fully populated Ms2Enhancer instance.
            analysis: Main analysis dataset loaded from project test directory.
            logger: A contextual logger.
        """
        assert analysis.spectra is not None, "Analysis should have spectra to pass to MS2 enhancer."
        original_spectra_count = len(analysis.spectra)

        start = time()
        enriched = ms2_enhancer.enhance(analysis, chunk_size=1000)
        logger.info(f"Enhanced {len(enriched.spectra)} MS2 spectra in {time() - start:.2f} seconds")

        assert enriched is not None, "A valid Analysis must be returned."
        assert len(enriched.spectra) == original_spectra_count, "The same number of spectra must be returned."

