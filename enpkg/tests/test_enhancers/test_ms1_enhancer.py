"""Test suite for the MS1 Enhancer."""

import logging
from time import time
from typing import Any, List

import pytest

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.ms1_data_classes.adduct_class import ChemicalAdduct
from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.lotus_store import LotusStore
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
def lotus_store(ms_enhancer_config: MSEnhancerConfig, logger: logging.Logger) -> LotusStore:
    return LotusStore(
        duckdb_path=ms_enhancer_config.downloader_params.duckdb_path,
        logger=logger,
    )


@pytest.fixture(scope="class")
def ms1_enhancer(
    ms_enhancer_config: MSEnhancerConfig,
    logger: logging.Logger,
    lotus_store: LotusStore,
) -> MS1Enhancer:
    return MS1Enhancer(
        configuration=ms_enhancer_config, logger=logger, lotus_store=lotus_store,
    )


@pytest.fixture(scope="class")
def lotus_objects(ms1_enhancer: MS1Enhancer, analysis: Any, logger: logging.Logger):
    # With the LotusStore-backed path, initialize_lotus_objects now REQUIRES a
    # spectrum list so it can derive the reachable mass window. We no longer
    # pickle-cache the result: the query is a fast DuckDB range scan, and the
    # cache hid staleness bugs when the toy dataset or DB file changed.
    logger.info("Computing LOTUS objects via LotusStore mass-window query...")
    start = time()
    objects = ms1_enhancer.initialize_lotus_objects(spectrum_list=analysis.spectra)
    logger.info(f"Computed {len(objects)} formula groups in {time() - start:.2f} seconds")
    return objects


class TestMS1Enhancer:
    """Test class for MS1Enhancer."""

    def test_initialize_lotus_objects(self, lotus_objects: List[Any], logger: logging.Logger) -> None:
        """Test that LOTUS objects are properly initialized.
        
        Args:
            lotus_objects: Cache-loaded or computed LOTUS object list.
            logger: A logger instance.
        """
        assert lotus_objects is not None, "LOTUS objects should not be None."
        assert len(lotus_objects) > 0, "There should be at least one LOTUS object grouped list."
        logger.debug(f"Sample LOTUS object: {lotus_objects[0][0]}")

    def test_initialize_adducts(self, ms1_enhancer: MS1Enhancer, lotus_objects: List[Any], logger: logging.Logger) -> None:
        """Test that adducts are properly initialized dynamically based on LOTUS formulas.
        
        Args:
            ms1_enhancer: Unconfigured MS1 enhancer instance.
            lotus_objects: Source list of LOTUS objects.
            logger: A logger instance.
        """
        start = time()
        adducts = ms1_enhancer.initialize_adducts(lotus_objects)
        logger.info(f"Created {len(adducts)} adducts in {time() - start:.2f} seconds")
        
        assert adducts is not None, "Adducts should not be None."
        assert len(adducts) > 0, "Should generate at least one valid adduct."
        assert all(adduct is not None for adduct in adducts), "No generated adduct should be None."
        assert all(isinstance(adduct, ChemicalAdduct) for adduct in adducts), "All initialized objects must be valid ChemicalAdducts."

    def test_enhance_analysis(self, ms1_enhancer: MS1Enhancer, lotus_objects: List[Any], analysis: Any) -> None:
        """Test the full enrichment pipeline for ms1 spectra.
        
        Args:
            ms1_enhancer: Tested enhancer instance.
            lotus_objects: Tested LOTUS objects cache.
            analysis: Parsed analysis dataset containing parsed and grouped spectra.
        """
        ms1_enhancer._adducts = ms1_enhancer.initialize_adducts(lotus_objects)
        enhanced_spectra = ms1_enhancer.enhance(analysis.spectra)

        assert enhanced_spectra is not None, "A valid list of enhanced spectra must be returned."
        assert len(enhanced_spectra) == len(analysis.spectra), "The number of enhanced spectra should strictly match the initial count."

    def test_initialize_adducts_empty_list(self, ms1_enhancer: MS1Enhancer) -> None:
        """Test bounds behavior when attempting to initialize empty subsets.
        
        Args:
            ms1_enhancer: Enhancer processing edge case.
        """
        # Testing if empty array breaks execution or returns gracefully back
        adducts = ms1_enhancer.initialize_adducts([])
        assert isinstance(adducts, list)
        assert len(adducts) == 0, "No adducts should be generated for an empty list."
