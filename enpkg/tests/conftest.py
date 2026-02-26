# Cache directory for expensive computations
from pathlib import Path

import logging
import pytest

from enpkg.monolith.loaders.analysis_loader import AnalysisLoader


CACHE_DIR = Path(__file__).parent / ".test_cache"
DATABASE_DIR = Path(__file__).parent / ".databases"
TEST_DATA_DIR = Path(__file__).parent / "test-data"


@pytest.fixture(scope="session")
def logger():
    """Create a logger for tests."""
    logging.basicConfig(level=logging.DEBUG)
    return logging.getLogger(__name__)


@pytest.fixture(scope="session")
def analysis():
    """Load a test analysis."""

    return AnalysisLoader.from_files(
        path_to_spectra=TEST_DATA_DIR / "enpkg_toy_dataset/msdata/processed/VGF151_E05_pos.mgf",
        path_to_metadata=TEST_DATA_DIR / "enpkg_toy_dataset/metadata/metadata.tsv",
        path_to_quant_table=TEST_DATA_DIR / "enpkg_toy_dataset/msdata/processed/VGF151_E05_pos_quant.csv",
        ionization_mode="pos",
    )

