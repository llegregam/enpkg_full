"""Test suite for the MS1 Enhancer."""

import pickle
from time import time

import pytest

from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.data.ms1_data_classes.adduct_class import ChemicalAdduct
from enpkg.monolith.configuration.MSEnhancer_config import (
    MSEnhancerConfig,
    GeneralParams,
    Urls,
    Paths,
)

from .conftest import CACHE_DIR, DATABASE_DIR

LOTUS_CACHE_PATH = CACHE_DIR / "lotus_grouped_by_structure_molecular_formula.pkl"


@pytest.fixture(scope="class")
def ms1_config():
    """Create MS1 enhancer configuration."""
    paths = Paths()
    urls = Urls(
        taxo_db_metadata="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
        taxo_db_pathways="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
        taxo_db_superclasses="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
        taxo_db_classes="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1",
        spectral_db_pos="https://zenodo.org/records/8287341/files/isdb_pos_cleaned.pkl",
    )
    return MSEnhancerConfig(
        general_params=GeneralParams(
            redownload_if_exists=False,
            download_dir=str(DATABASE_DIR),
            polarity="pos",
        ),
        urls=urls,
        paths=paths,
    )


@pytest.fixture(scope="class")
def ms1_enhancer(ms1_config, logger):
    """Create the MS1 enhancer instance."""
    return MS1Enhancer(configuration=ms1_config, logger=logger)


@pytest.fixture(scope="class")
def lotus_objects(ms1_enhancer, logger):
    """Load or compute LOTUS objects with caching."""

    CACHE_DIR.mkdir(exist_ok=True)
    
    if LOTUS_CACHE_PATH.exists():
        logger.info("Loading LOTUS objects from cache...")
        start = time()
        with open(LOTUS_CACHE_PATH, "rb") as f:
            lotus_objects = pickle.load(f)
        logger.info(f"Loaded LOTUS objects from cache in {time() - start:.2f} seconds")
    else:
        logger.info("Computing LOTUS objects (first run)...")
        lotus_objects = ms1_enhancer.initialize_lotus_objects()
        start = time()
        logger.info("Dumping LOTUS objects to cache with pickle")
        with open(LOTUS_CACHE_PATH, "wb") as f:
            pickle.dump(lotus_objects, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info(f"Finished dumping LOTUS objects in {time() - start:.2f} seconds")
    
    return lotus_objects


class TestMS1Enhancer:
    """Test class for MS1Enhancer."""

    def test_initialize_lotus_objects(self, lotus_objects, logger):
        """Test that LOTUS objects are properly initialized."""
        assert lotus_objects is not None
        assert len(lotus_objects) > 0
        logger.debug(f"Sample LOTUS object: {lotus_objects[0][0]}")

    def test_initialize_adducts(self, ms1_enhancer, lotus_objects, logger):
        """Test that adducts are properly initialized."""
        start = time()
        adducts = ms1_enhancer.initialize_adducts(lotus_objects)
        logger.info(f"Created {len(adducts)} adducts in {time() - start:.2f} seconds")
        
        assert adducts is not None
        assert len(adducts) > 0
        assert all(adduct is not None for adduct in adducts)
        assert all(isinstance(adduct, ChemicalAdduct) for adduct in adducts)

    def test_enhance_analysis(self, ms1_enhancer, lotus_objects, analysis):
        """Test the full enrichment pipeline."""

        # Initialize adducts
        ms1_enhancer._adducts = ms1_enhancer.initialize_adducts(lotus_objects)
        
        # Run enhancement
        enhanced_spectra = ms1_enhancer.enhance(analysis.spectra)

        # Check that enhanced spectra are returned and have the same length as the input spectra
        assert enhanced_spectra is not None
        assert len(enhanced_spectra) == len(analysis.spectra)

