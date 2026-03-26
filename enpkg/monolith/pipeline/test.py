import logging

from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.pipeline.taxonomical_enhancement_step import TaxonomicalEnhancementStep
from enpkg.monolith.pipeline.molecular_networking_step import MolecularNetworkingStep
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.pipeline.ms1_enhancement_step import MS1EnhancementStep
from enpkg.monolith.pipeline.ms2_enhancement_step import MS2EnrichmentStep
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.configuration.MSEnhancer_config import (
    DownloaderParams,
    MSEnhancerConfig,
    GeneralParams,
    Urls,
    Paths,
)
from enpkg.monolith.loaders.database_loader import DBLoader

from enpkg.tests.conftest import DATABASE_DIR

def main():
    import random
    import polars

    logger = logging.getLogger(__name__)
    file_handler = logging.FileHandler("./test_pipeline.log", mode="w")
    stream_handler = logging.StreamHandler()
    logger.setLevel(logging.DEBUG)
    file_handler.setLevel(logging.DEBUG)
    stream_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    number_of_test_spectra = 50

    # Load analysis data
    analysis = AnalysisLoader.from_files(
        path_to_spectra="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/test-data/actea_EtOAc-1_pos.mgf",
        path_to_metadata="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/test-data/qualome_metadata.txt",
        path_to_quant_table="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/test-data/actea_EtOAc-1_pos_quant.csv",
        ionization_mode="pos"
    )

    test_spectra_indices = []
    while len(set(test_spectra_indices)) < number_of_test_spectra:
        test_spectra_indices = [random.randint(0, len(analysis.spectra) - 1) for _ in range(number_of_test_spectra+1) if random.randint(0, len(analysis.spectra) - 1) not in test_spectra_indices]
    
    logger.info(f"List of randomly selected spectra indices for testing: {test_spectra_indices}")
    analysis.spectra = [analysis.spectra[i] for i in test_spectra_indices]

    # Initialize pipeline steps
    print("Running taxonomical enrichment step...")
    taxonomical_enrichment_step = TaxonomicalEnhancementStep()
    if taxonomical_enrichment_step.can_run(analysis):
        analysis = taxonomical_enrichment_step.process(analysis)

        assert len(analysis.ott_matches) > 0, "Taxonomical enrichment should have added OTT matches."
        for match in analysis.ott_matches:
            assert isinstance(match, Match), "OTT matches should be instances of Match class."
            logger.info(f"OTT Match: {match.taxon.name} (OTT ID: {match.ott_id})")
            logger.info(
                f"Lineage: Domain={match.domain}\n"
                f"Kingdom={match.kingdom}\n"
                f"Phylum={match.phylum}\n"
                f"Class={match.klass}\n"
                f"Order={match.order}\n"
                f"Family={match.family}\n"
                f"Genus={match.genus}\n"
                f"Species={match.species}"
            )
            logger.info(f"Lineage details: {match.lineage}")

    else:
        logger.info("Taxonomical enrichment step cannot run on this analysis.")

    print("Running molecular networking step...")
    molecular_networking_step = MolecularNetworkingStep(
        NetworkEnhancerConfig()
    )
    if molecular_networking_step.can_run(analysis):
        analysis = molecular_networking_step.process(analysis)
        assert analysis.molecular_network is not None, "Molecular network should be created."
        logger.info(f"Molecular network created with {len(analysis.molecular_network.nodes)} nodes and {len(analysis.molecular_network.edges)} edges.")
        assert len(analysis.molecular_network.nodes) == analysis.number_of_spectra, "Number of nodes in the molecular network should match the number of spectra."
        for node, spectra in zip(analysis.molecular_network.nodes, analysis.spectra):
            assert node == spectra.get("feature_id"), "Node identifiers in the molecular network should match feature IDs of spectra."
        logger.info(f"Molecular network created with {len(analysis.molecular_network.nodes)} nodes and {len(analysis.molecular_network.edges)} edges.")
    else:
        logger.info("Molecular networking step cannot run on this analysis.")

    paths = Paths()
    urls = Urls(
        taxo_db_metadata="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
        taxo_db_pathways="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
        taxo_db_superclasses="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
        taxo_db_classes="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1",
        spectral_db_pos="https://zenodo.org/records/8287341/files/isdb_pos_cleaned.pkl",
    )
    ms_config = MSEnhancerConfig(
            downloader_params=DownloaderParams(
                redownload_if_exists=False,
                download_dir=str(DATABASE_DIR),
                urls=urls,
                paths=paths,
            )
        )
    db_loader = DBLoader(configuration=ms_config, logger=logger)

    logger.info("Running MS1 enhancement step...")
    ms1_enhancement_step = MS1EnhancementStep(
        logger=logger,
        config=ms_config,
        db_loader=db_loader
    )
    if ms1_enhancement_step.can_run(analysis):
        analysis = ms1_enhancement_step.process(analysis)
        assert len(analysis.spectra) > 0, "MS1 enhancement should not remove spectra from the analysis."
        for spectrum in analysis.spectra:
            logger.debug(f"Spectrum {spectrum.get('feature_id')}")

            for i, annotation in enumerate(spectrum.get("ms1_annotations", [])):
                logger.debug(f"MS1 annotation {i}: {annotation}")
        logger.info(f"MS1 enhancement completed. Number of spectra after enhancement: {len(analysis.spectra)}")
    else:
        logger.info("MS1 enhancement step cannot run on this analysis.")

    ms2_enhancement_step = MS2EnrichmentStep(
        config=ms_config,
        logger=logger,
        db_loader=db_loader
    )
    if ms2_enhancement_step.can_run(analysis):
        analysis = ms2_enhancement_step.process(analysis)
        assert len(analysis.spectra) > 0, "MS2 enhancement should not remove spectra from the analysis."
        logger.info(f"MS2 enhancement completed. Number of spectra after enhancement: {len(analysis.spectra)}")
        for spectrum in analysis.spectra:
            logger.debug(f"Spectrum {spectrum.get('feature_id')}")
            for i, annotation in enumerate(spectrum.ms2_annotations):
                logger.debug(f"MS2 annotation {i}: {annotation}")
    else:
        logger.info("MS2 enhancement step cannot run on this analysis.")

    

if __name__ == "__main__":

    main()
    # analysis = AnalysisLoader.from_files(
    #     path_to_spectra="/home/llegregam/git_projects/enpkg_full/monolith/data/tests/actea_EtOAc-1_pos.mgf",
    #     path_to_metadata="/home/llegregam/git_projects/enpkg_full/monolith/data/tests/qualome_metadata.tsv",
    #     ionization_mode="pos"
    # )

    # taxonomical_enrichment_step = TaxonomicalEnrichmentStep()
    # molecular_networking_step = MolecularNetworkingStep(
    #     NetworkEnhancerConfig(
    #         mn_msms_mz_tol=0.01,
    #         mn_score_cutoff=0.7,
    #         mn_top_n=15,
    #         mn_max_links=10
    #     )
    # )
    
    # # for step in [taxonomical_enrichment_step, molecular_networking_step]:
    # for step in [molecular_networking_step]:
    #     print(f"Running step: {step.name()}")
    #     if step.can_run(analysis):
    #         analysis = step.process(analysis)
    #         print(f"Step {step.name()} completed.")
    #         step.export_components(analysis, "/home/llegregam/git_projects/enpkg_full/src/monolith/pipeline/test_components.tsv")
    #     else:
    #         print(f"Step {step.name()} cannot run on this analysis.")

        
    
    # print(f"OTT matches for analysis: {analysis.ott_matches}")
    # print(f"Loaded {len(analysis.spectra)} spectra from MGF file.")
    # print(f"Molecular network nodes: {analysis.molecular_network.nodes}")