"""Test suite for the MS1 Enhancer."""

import logging
import os
import pickle
from pathlib import Path
from time import time
from typing import Any, List

import pytest

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.ms1_data_classes.adduct_class import ChemicalAdduct
from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.database_loader import DBLoader

if "PROJECT_ROOT" in os.environ:
    PROJECT_ROOT = Path(os.environ["PROJECT_ROOT"])
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

CACHE_DIR = PROJECT_ROOT / "enpkg" / "tests" / "test_enhancers" / ".test_cache"
TEST_DATA_DIR = PROJECT_ROOT / "data" / "input"


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
def db_loader(ms_enhancer_config: MSEnhancerConfig, logger: logging.Logger) -> DBLoader:
    return DBLoader(configuration=ms_enhancer_config, logger=logger)


@pytest.fixture(scope="class")
def ms1_enhancer(
    ms_enhancer_config: MSEnhancerConfig,
    logger: logging.Logger,
    db_loader: DBLoader,
) -> MS1Enhancer:
    return MS1Enhancer(configuration=ms_enhancer_config, logger=logger, db_loader=db_loader)


@pytest.fixture(scope="class")
def lotus_objects(ms1_enhancer: MS1Enhancer, logger: logging.Logger):
    lotus_cache_path = CACHE_DIR / "lotus_grouped_by_structure_molecular_formula.pkl"
    CACHE_DIR.mkdir(exist_ok=True)

    if lotus_cache_path.exists():
        logger.info("Loading LOTUS objects from cache...")
        start = time()
        with open(lotus_cache_path, "rb") as handle:
            objects = pickle.load(handle)
        logger.info(f"Loaded LOTUS objects from cache in {time() - start:.2f} seconds")
    else:
        logger.info("Computing LOTUS objects (first run)...")
        objects = ms1_enhancer.initialize_lotus_objects()
        start = time()
        logger.info("Dumping LOTUS objects to cache with pickle")
        with open(lotus_cache_path, "wb") as handle:
            pickle.dump(objects, handle, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info(f"Finished dumping LOTUS objects in {time() - start:.2f} seconds")

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
