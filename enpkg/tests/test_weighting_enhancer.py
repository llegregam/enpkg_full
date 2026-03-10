import pytest

from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.enhancers.weights_enhancer import WeightsEnhancer
from enpkg.monolith.enhancers.taxa_enhancer import TaxaEnhancer
from enpkg.monolith.enhancers.network_enhancer import NetworkEnhancer
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.configuration.MSEnhancer_config import (
    DownloaderParams,
    GeneralParams,
    Urls,
    Paths,
)

from .conftest import CACHE_DIR, DATABASE_DIR


# Run test with following command:
# pytest -v -s enpkg/tests/test_weighting_enhancer.py

@pytest.fixture(scope="class")
def reweighting_config():
    paths = Paths()
    urls = Urls(
        taxo_db_metadata="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
        taxo_db_pathways="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
        taxo_db_superclasses="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
        taxo_db_classes="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1"
    )
    return ReweightingConfig(
        general_params=GeneralParams(
            recompute=False,
            polarity="pos",
        ),
        downloader_params=DownloaderParams(
            redownload_if_exists=False,
            download_dir=str(DATABASE_DIR),
            urls=urls,
            paths=paths,
        )
    )

@pytest.fixture(scope="class")
def enhanced_analysis(analysis):
    
    genus, species = analysis.genus_and_species
    enhancer = TaxaEnhancer()
    new_matches = enhancer.enhance(genus, species)
    analysis.ott_matches += new_matches
    net_enhancer = NetworkEnhancer(NetworkEnhancerConfig())
    analysis = net_enhancer.enhance(analysis)
    return analysis

class TestWeightsEnhancer:

    def test_weights_enhancer_initialization(self, reweighting_config, logger, enhanced_analysis):
        """Test that the WeightsEnhancer initializes correctly with a given configuration and logger."""
        
        enhancer = WeightsEnhancer(configuration=reweighting_config, logger=logger)

        final_enhanced_analysis = enhancer.enhance(enhanced_analysis)
        # assert enhanced_analysis is not None
        # assert len(enhanced_analysis.ott_matches) > 0