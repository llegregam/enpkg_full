"""Submodule for the MS1 level enhancer, which adds the Adducts to a given batch and computes its LPA scores."""

from time import time
from typing import Optional
from logging import Logger
import pandas as pd
import numpy as np

from tqdm.auto import tqdm

from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.enhancers.adducts import POSITIVE_RECIPES, NEGATIVE_RECIPES
from enpkg.monolith.data.analysis import Analysis, AnnotatedSpectrum
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.data.ms1_data_classes import ChemicalAdduct, MS1EnhancerConfig
from enpkg.monolith.utils import binary_search_by_key, label_propagation_algorithm
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig, GeneralParams, Urls, Paths
from enpkg.monolith.loaders.database_loader import DBLoader


class MS1Enhancer(Enhancer):
    """Enhancer that adds MS1 information to the analysis."""

    def __init__(self, configuration: MSEnhancerConfig, logger: Logger):
        """Initializes the enhancer."""

        if not isinstance(configuration, MSEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type MSEnhancerConfig, got {type(configuration)}"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"Expected logger of type logging.Logger, got {type(logger)}")
        
        self.configuration = configuration
        self.logger = logger

        self.logger.info("Loading Databases")
        self.databases = DBLoader(configuration=configuration, logger=logger) # use the db_loader to get db paths to ensure they are downloaded
        self.databases.load_taxonomical_databases()
        # lotus_grouped_by_structure_molecular_formula = self._initialize_lotus_objects()
        # self.logger.info("Finished loading databases and initializing LOTUS objects")   
        # self.logger.debug(f"Sample LOTUS object: {lotus_grouped_by_structure_molecular_formula[0][0]}") 
        # self.logger.info("Initializing adducts")
        # start = time()
        # self._adducts = self._initialize_adducts(lotus_grouped_by_structure_molecular_formula)
        # self.logger.info(
        #     "Created %d adducts in %.2f seconds",
        #     len(self._adducts),
        #     time() - start,
        # )

    def initialize_adducts(self, lotus_grouped_by_structure_molecular_formula: list[list[Lotus]]) -> list[ChemicalAdduct]:
        """
        Initialize the adducts by creating a ChemicalAdduct object for each LOTUS group and each recipe, and then sorting them by their adduct mass.
        This function performs the following steps:
            1. Iterates over the LOTUS groups (grouped by molecular formula) and over the recipes (positive or negative depending on the configuration), creating a ChemicalAdduct object for each combination.
            2. Sorts the adducts by their adduct mass to allow for efficient binary search when matching precursor masses with adducts during enrichment.
        returns: A sorted list of ChemicalAdduct objects.
        """
        adducts: list[ChemicalAdduct] = (
            [
                ChemicalAdduct(
                    lotus=lotus_group,
                    recipe=recipe,
                )
                for lotus_group in lotus_grouped_by_structure_molecular_formula
                for recipe in POSITIVE_RECIPES
            ]
            if self.configuration.general_params.polarity == "pos"
            else [
                ChemicalAdduct(
                    lotus=lotus_group,
                    recipe=recipe,
                )
                for lotus_group in lotus_grouped_by_structure_molecular_formula
                for recipe in NEGATIVE_RECIPES
            ]
        )

        # We sort the adducts by the 'adduct_mass' key so that when
        # we match the precursor mass with the adducts, we can do so
        # via binary search.
        adducts = sorted(adducts, key=lambda x: x.adduct_mass)

        return adducts

    def initialize_lotus_objects(self) -> list[list[Lotus]]:
        """
        Initialize the LOTUS objects and group them by their molecular formula.
            This function performs the following steps:
            1. Validates that the LOTUS metadata is not empty and that all entries have the same molecular formula.
            2. Transposes the classification DataFrames for efficient access by SMILES key.
            3. Iterates over the LOTUS metadata grouped by molecular formula, creating LOTUS objects for each entry and grouping them by their molecular formula.
            4. Caches the SMILES lookup within the inner loop to avoid redundant index operations.
        
        returns: List of lists of LOTUS objects, where each inner list contains LOTUS entries with the same molecular formula.
        """

        structure_smiles_col: int = self.databases.lotus_metadata.columns.get_loc(
            "structure_smiles"
        )
        
        # Initialize class-level column mappings for efficient Lotus object creation
        # This allows Lotus.from_pandas_series to know which index corresponds to which field
        Lotus.setup_lotus_columns(list(self.databases.lotus_metadata.columns))
        
        # Transpose classification DataFrames for O(1) column access by SMILES key
        # Original shape: (num_classes, num_compounds) with SMILES as columns
        # Transposed: (num_compounds, num_classes) - but we access by column name (SMILES)
        # This is more efficient than .to_dict() which copies all data into Python dicts
        start = time()
        
        pathways_t = self.databases.lotus_metadata_pathways.T
        superclasses_t = self.databases.lotus_metadata_superclasses.T
        classes_t = self.databases.lotus_metadata_classes.T
        self.logger.debug(f"Transposed classification DataFrames in {time() - start:.2f} seconds")
        
        # Build nested structure: group all LOTUS entries by molecular formula
        # Outer loop: iterate over groups (one per unique molecular formula)
        # Inner loop: iterate over rows within each group (individual compounds/isomers)
        # 
        # The `for smiles in (row[...],)` pattern creates a single-element tuple,
        # effectively caching the SMILES lookup to avoid 3 separate index operations
        start = time()
        # TODO: Bottleneck, needs optimization. Maybe when we build the real db.
        lotus_grouped_by_structure_molecular_formula: list[list[Lotus]] = [
            [
                Lotus.from_pandas_series(
                    list(row),  # Convert numpy row to list for Lotus constructor
                    pathways=pathways_t[smiles],        # NPC pathway probability distribution
                    superclasses=superclasses_t[smiles], # NPC superclass probability distribution  
                    classes=classes_t[smiles],           # NPC class probability distribution
                )
                for row in group.values
                for smiles in (row[structure_smiles_col],)  # Cache SMILES lookup
            ]
            for (_, group) in tqdm(
                self.databases.lotus_metadata.groupby(by=["structure_molecular_formula"]),
                desc="Initializing LOTUS objects",
                dynamic_ncols=True,
                leave=False,
            )
        ]
        self.logger.debug(f"Built {len(lotus_grouped_by_structure_molecular_formula)} LOTUS groups in {time() - start:.2f} seconds")

        return lotus_grouped_by_structure_molecular_formula


    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS1 Enhancer"

    def enhance(self, spectrum_list: list[AnnotatedSpectrum]) -> list[AnnotatedSpectrum]:
        """Adds MS1 information to the analysis."""

        self.logger.info("Running enrichment process")
        number_of_spectra = len(spectrum_list)
        self.logger.info(f"Number of spectra to enrich: {number_of_spectra}")
        self._number_of_pathways = self.databases.lotus_metadata_pathways.shape[1]
        self._number_of_superclasses = self.databases.lotus_metadata_superclasses.shape[1]
        self._number_of_classes = self.databases.lotus_metadata_classes.shape[1]

        if not hasattr(self, "_adducts"):
            self.logger.info("Initializing LOTUS objects and adducts for the first time")
            start = time()
            lotus_grouped_by_structure_molecular_formula = self.initialize_lotus_objects()
            self._adducts = self.initialize_adducts(lotus_grouped_by_structure_molecular_formula)
            self.logger.info(
                f"Initialized {len(self._adducts)} adducts in {time() - start:.2f} seconds"
            )
        else:
            self.logger.info("Using pre-initialized adducts")

        for i, spectrum in tqdm(
            enumerate(spectrum_list),
            leave=False,
            total=number_of_spectra,
            desc="Filtering precursor adducts",
            dynamic_ncols=True,
        ):
            lower_mass_bound = (
                spectrum.precursor_mz
                - spectrum.precursor_mz * self.configuration.spectral_match_params.parent_mz_tol
            )
            upper_mass_bound = (
                spectrum.precursor_mz
                + spectrum.precursor_mz * self.configuration.spectral_match_params.parent_mz_tol
            )

            # Find the lower bound by exploring the sorted adducts via binary search
            (_, lower_mass_bound_index) = binary_search_by_key(
                key=lower_mass_bound,
                array=self._adducts,
                key_func=lambda adduct: adduct.adduct_mass,
            )

            # Find the upper bound by linear search starting from the identified lower
            # bound index and iterating up until we wncounter an adduct with an exact mass
            # greater than the upper bound
            upper_mass_bound_index = lower_mass_bound_index

            while upper_mass_bound > self._adducts[upper_mass_bound_index].adduct_mass:
                upper_mass_bound_index += 1

                if upper_mass_bound_index == len(self._adducts):
                    break

            spectrum.ms1_annotations = self._adducts[lower_mass_bound_index:upper_mass_bound_index]
        return spectrum_list
    
        # best_ott_match: Optional[Match] = analysis.best_ott_match

        # for i, spectrum in tqdm(
        #     enumerate(analysis.tandem_mass_spectra),
        #     leave=False,
        #     total=analysis.number_of_spectra,
        #     desc="Computing MS1 NPC scores",
        #     dynamic_ncols=True,
        # ):
        #     # If the spectrum has no adducts, we cannot make assumptions regarding its scores,
        #     # and therefore we give uniform scores to all pathways, superclasses, and classes.
        #     if not spectrum.has_ms1_annotations():
        #         pathway_features[i] = np.zeros(
        #             shape=(self._number_of_pathways,),
        #         )
        #         superclass_features[i] = np.zeros(
        #             shape=(self._number_of_superclasses,),
        #         )
        #         class_features[i] = np.zeros(
        #             shape=(self._number_of_classes,),
        #         )
        #         continue

        #     # Now that we have determined the adducts potentially associated with this
        #     # spectrum, we can populate the associated features with the adducts' pathway,
        #     # superclass, and class annotations, weighted by the adduct's normalized
        #     # taxonomical similarity score.

        #     # First, we compute the maximal normalized taxonomical similarity score for
        #     # each adducts, if we do have a known sample taxonomy match.
        #     if best_ott_match is not None:
        #         taxonomical_similarities: np.ndarray = np.fromiter(
        #             (
        #                 adduct.maximal_normalized_taxonomical_similarity(best_ott_match)
        #                 for adduct in spectrum.ms1_annotations
        #             ),
        #             dtype=np.float32,
        #         )
        #     else:
        #         taxonomical_similarities: np.ndarray = np.ones(
        #             shape=(len(spectrum.ms1_annotations),), dtype=np.float32
        #         )

        #     total_taxonomical_similarities = np.sum(taxonomical_similarities)
        #     if total_taxonomical_similarities > 0:
        #         taxonomical_similarities /= total_taxonomical_similarities

        #     for taxonomical_similarity, adduct in zip(
        #         taxonomical_similarities, spectrum.ms1_annotations
        #     ):
        #         pathway_features[i] += (
        #             taxonomical_similarity * adduct.get_hammer_pathway_scores()
        #         )

        #         superclass_features[i] += (
        #             taxonomical_similarity * adduct.get_hammer_superclass_scores()
        #         )

        #         class_features[i] += (
        #             taxonomical_similarity * adduct.get_hammer_class_scores()
        #         )

        # # THIS SHOULD BE DELETED AFTERWARDS! DO NOT KEEP THIS!
        # # SHOULD BE ELSEWHERE

        # pathway = pd.DataFrame(pathway_features, columns=self._pathways)
        # pathway.to_csv("downloads/before_lpa_ms1_pathway.csv", index=False)
        # superclass = pd.DataFrame(superclass_features, columns=self._superclasses)
        # superclass.to_csv("downloads/before_lpa_ms1_superclass.csv", index=False)
        # classes = pd.DataFrame(class_features, columns=self._classes)
        # classes.to_csv("downloads/before_lpa_ms1_class.csv", index=False)

        # loading_bar = tqdm(
        #     desc="Computing LPA scores",
        #     dynamic_ncols=True,
        #     leave=False,
        #     total=3,
        # )

        # propagated_pathway = label_propagation_algorithm(
        #     graph=analysis.molecular_network,
        #     node_names=analysis.feature_ids,
        #     features=pathway_features,
        #     normalize=False,
        # )

        # loading_bar.update(1)

        # propagated_superclass = label_propagation_algorithm(
        #     graph=analysis.molecular_network,
        #     node_names=analysis.feature_ids,
        #     features=superclass_features,
        #     normalize=False,
        # )

        # loading_bar.update(1)

        # propagated_class = label_propagation_algorithm(
        #     graph=analysis.molecular_network,
        #     node_names=analysis.feature_ids,
        #     features=class_features,
        #     normalize=False,
        # )

        # loading_bar.update(1)
        # loading_bar.close()

        # for i, spectrum in enumerate(analysis.tandem_mass_spectra):
        #     spectrum.set_ms1_hammer_pathway_scores(propagated_pathway[i])
        #     spectrum.set_ms1_hammer_superclass_scores(propagated_superclass[i])
        #     spectrum.set_ms1_hammer_class_scores(propagated_class[i])

        # # THIS SHOULD BE DELETED AFTERWARDS! DO NOT KEEP THIS!

        # pathway = pd.DataFrame(propagated_pathway, columns=self._pathways)
        # pathway.to_csv("downloads/ms1_pathway.csv", index=False)
        # superclass = pd.DataFrame(propagated_superclass, columns=self._superclasses)
        # superclass.to_csv("downloads/ms1_superclass.csv", index=False)
        # classes = pd.DataFrame(propagated_class, columns=self._classes)
        # classes.to_csv("downloads/ms1_class.csv", index=False)

        # return analysis


if __name__ == "__main__":
    # Run tests with: pytest enpkg/tests/test_ms1_enhancer.py -v
    import subprocess
    import sys
    
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "enpkg/tests/test_ms1_enhancer.py", "-v"],
        cwd="/home/llegregam/git_projects/enpkg_full",
    )
    sys.exit(result.returncode)

    # --- Original inline test code (kept for reference) ---
    # import logging
    # import pickle
    # from enpkg.monolith.data.ms1_data_classes.ms1_configuration_class import MS1EnhancerConfig
    # from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
    

    # logging.basicConfig(level=logging.DEBUG)
    # logger = logging.getLogger("MS1EnhancerTest")

    # paths = Paths(
    #     taxo_db_metadata="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb/taxo_db_metadata.csv",
    #     spectral_db_pos="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb/spectral_db_pos.pkl",
    #     taxo_db_pathways="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb/taxo_db_pathways.csv",
    #     taxo_db_superclasses="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb/taxo_db_superclasses.csv",
    #     taxo_db_classes="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb/taxo_db_classes.csv"
    # )
    # paths = Paths()
    # urls = Urls(
    #     taxo_db_metadata="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
    #     taxo_db_pathways="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
    #     taxo_db_superclasses="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
    #     taxo_db_classes="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1",
    #     spectral_db_pos="https://zenodo.org/records/8287341/files/isdb_pos_cleaned.pkl"
    # )

    # config = MSEnhancerConfig(
    #     general_params=GeneralParams(
    #         redownload_if_exists=False,
    #         download_dir="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb",
    #         polarity="pos"
        
    #     ),
    #     urls = urls,
    #     paths = paths
    # )
    # # TODO: Think about how ionization mode is set in the pipelines.
    # analysis = AnalysisLoader.from_files(
    #     path_to_spectra="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos.mgf",
    #     path_to_metadata="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/metadata/metadata.tsv",
    #     path_to_quant_table="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos_quant.csv",
    #     ionization_mode="pos"
    # )

    # enhancer = MS1Enhancer(configuration=config, logger=logger)
    
    # # Cache path for LOTUS objects
    # lotus_cache_path = "/home/llegregam/git_projects/enpkg_full/enpkg/monolith/enhancers/test_isdb/lotus_grouped_by_structure_molecular_formula.pkl"
    
    # # Load from cache if exists, otherwise compute and save
    # from pathlib import Path
    # if Path(lotus_cache_path).exists():
    #     logger.info("Loading LOTUS objects from cache...")
    #     start = time()
    #     with open(lotus_cache_path, "rb") as f:
    #         lotus_grouped_by_structure_molecular_formula = pickle.load(f)
    #     logger.info(f"Loaded LOTUS objects from cache in {time() - start:.2f} seconds")
    # else:
    #     logger.info("Computing LOTUS objects (first run)...")
    #     lotus_grouped_by_structure_molecular_formula = enhancer._initialize_lotus_objects()
    #     start = time()
    #     logger.info("Dumping LOTUS objects to file")
    #     with open(lotus_cache_path, "wb") as f:
    #         pickle.dump(lotus_grouped_by_structure_molecular_formula, f)
    #     logger.info(f"Finished dumping LOTUS objects in {time() - start:.2f} seconds")
    
    # logger.info("Finished loading databases and initializing LOTUS objects")
    # logger.debug(f"Sample LOTUS object: {lotus_grouped_by_structure_molecular_formula[0][0]}")
    # logger.info("Initializing adducts")
    # start = time()
    # enhancer._adducts = enhancer._initialize_adducts(lotus_grouped_by_structure_molecular_formula)
    # logger.info(
    #     "Created %d adducts in %.2f seconds",
    #     len(enhancer._adducts),
    #     time() - start,
    # )

    # enriched_analysis = enhancer.enrich(analysis=analysis)

