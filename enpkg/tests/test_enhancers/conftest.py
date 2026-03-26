import os
from pathlib import Path

import logging
import pytest
from dotenv import load_dotenv

from enpkg.monolith.configuration.MSEnhancer_config import (
    DownloaderParams,
    MSEnhancerConfig,
    GeneralParams,
    Paths,
    Urls,
)
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.enhancers.network_enhancer import NetworkEnhancer
from enpkg.monolith.enhancers.taxa_enhancer import TaxaEnhancer

def pytest_configure():
    numba_logger = logging.getLogger('numba')
    numba_logger.setLevel(logging.WARNING)  # or logging.ERROR to suppress even more

load_dotenv()

if "PROJECT_ROOT" in os.environ:
    PROJECT_ROOT = Path(os.environ["PROJECT_ROOT"])
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATABASE_DIR = PROJECT_ROOT / "enpkg" / "tests" / "test_enhancers" / ".databases"


@pytest.fixture(scope="session")
def logger():
    """Create a logger for tests."""
    logging.basicConfig(level=logging.DEBUG)
    return logging.getLogger("root")


@pytest.fixture(scope="session")
def common_urls() -> Urls:
    return Urls(
        taxo_db_metadata="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
        taxo_db_pathways="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
        taxo_db_superclasses="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
        taxo_db_classes="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1",
        spectral_db_pos="https://zenodo.org/records/8287341/files/isdb_pos_cleaned.pkl",
    )


@pytest.fixture(scope="session")
def ms_enhancer_config(common_urls: Urls) -> MSEnhancerConfig:
    return MSEnhancerConfig(
        general_params=GeneralParams(
            recompute=False,
            polarity="pos",
        ),
        downloader_params=DownloaderParams(
            redownload_if_exists=False,
            download_dir=str(DATABASE_DIR),
            urls=common_urls,
            paths=Paths(),
        ),
    )


@pytest.fixture(scope="session")
def reweighting_config(common_urls: Urls) -> ReweightingConfig:
    return ReweightingConfig(
        general_params=GeneralParams(
            recompute=False,
            polarity="pos",
        ),
        downloader_params=DownloaderParams(
            redownload_if_exists=False,
            download_dir=str(DATABASE_DIR),
            urls=common_urls,
            paths=Paths(),
        ),
    )


@pytest.fixture(scope="session")
def network_config() -> NetworkEnhancerConfig:
    return NetworkEnhancerConfig(
        mn_msms_mz_tol=0.01,
        mn_score_cutoff=0.7,
        mn_top_n=15,
        mn_max_links=10,
    )


@pytest.fixture(scope="session")
def network_enhancer(network_config: NetworkEnhancerConfig) -> NetworkEnhancer:
    return NetworkEnhancer(configuration=network_config)


@pytest.fixture(scope="session")
def taxa_enhancer() -> TaxaEnhancer:
    return TaxaEnhancer()
