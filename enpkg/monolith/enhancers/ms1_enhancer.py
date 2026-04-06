"""Submodule for the MS1 level enhancer, which adds the Adducts to a given batch and computes its LPA scores."""

from time import time
from typing import Optional
from logging import Logger
import pandas as pd
import numpy as np
import random

from tqdm.auto import tqdm

from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.enhancers.adducts import POSITIVE_RECIPES, NEGATIVE_RECIPES
from enpkg.monolith.data.analysis import Analysis, AnnotatedSpectrum
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.data.ms1_data_classes import ChemicalAdduct, MS1EnhancerConfig
from enpkg.monolith.utils import binary_search_by_key, label_propagation_algorithm
from enpkg.monolith.configuration.MSEnhancer_config import DownloaderParams, MSEnhancerConfig, GeneralParams, Urls, Paths
from enpkg.monolith.loaders.database_loader import DBLoader


class MS1Enhancer(Enhancer):
    """Enhancer that adds MS1 information to the analysis."""

    def __init__(self, configuration: MSEnhancerConfig, logger: Logger, db_loader: DBLoader):
        """Initializes the enhancer."""

        if not isinstance(configuration, MSEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type MSEnhancerConfig, got {type(configuration)}"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"Expected logger of type logging.Logger, got {type(logger)}")
        if not isinstance(db_loader, DBLoader):
            raise TypeError(f"Expected db_loader of type DBLoader, got {type(db_loader)}")
        
        self.configuration = configuration
        self.logger = logger

        self.logger.info("Loading Databases")
        self.db_loader = db_loader
        self.db_loader.load_taxonomical_databases()
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
        self.logger.debug(
            "Sample adducts (first 10): %s",
            "\n".join(
                f"  {i+1}. {adduct}" for i, adduct in enumerate(random.sample(adducts, min(10, len(adducts))))
            ),
        )

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

        structure_smiles_col: int = self.db_loader.lotus_metadata.columns.index(
            "structure_smiles"
        )
        
        # Initialize class-level column mappings for efficient Lotus object creation
        # This allows Lotus.from_polars_row to know which index corresponds to which field
        Lotus.setup_lotus_columns(list(self.db_loader.lotus_metadata.columns))
        
        # Convert classification DataFrames to O(1) dictionary lookups mapped by SMILES key.
        # Original shape: (num_compounds, num_classes) where first column is SMILES.
        # We store them as {SMILES: np.array([class_values])}
        start = time()
        
        pathways_t = {row[0]: np.array(row[1:]) for row in self.db_loader.lotus_metadata_pathways.iter_rows()}
        self.logger.debug(f"Random sample of built pathways_t entries: {random.sample(list(pathways_t.items()), 5)}")
        superclasses_t = {row[0]: np.array(row[1:]) for row in self.db_loader.lotus_metadata_superclasses.iter_rows()}
        classes_t = {row[0]: np.array(row[1:]) for row in self.db_loader.lotus_metadata_classes.iter_rows()}
        self.logger.debug(f"Built classification dictionaries DataFrames in {time() - start:.2f} seconds")
        
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
                Lotus.from_polars_row(
                    list(row),  # Convert polars row to list for Lotus constructor
                    pathways=pathways_t[smiles],        # NPC pathway probability distribution
                    superclasses=superclasses_t[smiles], # NPC superclass probability distribution  
                    classes=classes_t[smiles],           # NPC class probability distribution
                )
                for row in group.iter_rows()
                for smiles in (row[structure_smiles_col],)  # Cache SMILES lookup
            ]
            for name, group in tqdm(
                self.db_loader.lotus_metadata.group_by("structure_molecular_formula"),
                desc="Initializing LOTUS objects",
                dynamic_ncols=True,
                leave=False,
            )
        ]
        self.logger.debug(f"Built {len(lotus_grouped_by_structure_molecular_formula)} LOTUS groups in {time() - start:.2f} seconds")
        self.logger.debug(f"Sample of 5 LOTUS groups: {random.sample(lotus_grouped_by_structure_molecular_formula, min(5, len(lotus_grouped_by_structure_molecular_formula)))}")
        return lotus_grouped_by_structure_molecular_formula


    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS1 Enhancer"

    def enhance(self, spectrum_list: list[AnnotatedSpectrum]) -> list[AnnotatedSpectrum]:
        """Adds MS1 information to the analysis."""

        self.logger.info("Running enrichment process")
        number_of_spectra = len(spectrum_list)
        self.logger.info(f"Number of spectra to enrich: {number_of_spectra}")
        self._number_of_pathways = self.db_loader.lotus_metadata_pathways.shape[1] - 1
        self._number_of_superclasses = self.db_loader.lotus_metadata_superclasses.shape[1] - 1
        self._number_of_classes = self.db_loader.lotus_metadata_classes.shape[1] - 1

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
        self.logger.debug(f"Showing sample of spectrA with MS1 annotations after enrichment:")
        for i, spectrum in enumerate(random.sample(spectrum_list, k=10)):
            for annotation in spectrum.ms1_annotations:
                self.logger.debug(
                    f"Spectrum {spectrum.feature_id} (precursor m/z: {spectrum.precursor_mz}) matched with adduct {annotation.recipe} of LOTUS entry {annotation.lotus[0].structure_name_traditional} (adduct mass: {annotation.adduct_mass})"
                )
        return spectrum_list
    

if __name__ == "__main__":
    # Run tests with: pytest enpkg/tests/test_ms1_enhancer.py -v
    # import subprocess
    # import sys
    
    # result = subprocess.run(
    #     [sys.executable, "-m", "pytest", "enpkg/tests/test_ms1_enhancer.py", "-v"],
    #     cwd="/home/llegregam/git_projects/enpkg_full",
    # )
    # sys.exit(result.returncode)

    # --- Original inline test code (kept for reference) ---
    import logging
    # import pickle
    from enpkg.monolith.data.ms1_data_classes.ms1_configuration_class import MS1EnhancerConfig
    from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
    from enpkg.monolith.pipeline.test_pol import load_config

    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("MS1EnhancerTest")

    config = load_config()
    logger.info(f"Using configuration:\n{config}")
    # TODO: Think about how ionization mode is set in the pipelines.
    analysis = AnalysisLoader.from_files(
        path_to_spectra="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos.mgf",
        path_to_metadata="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/metadata/metadata.tsv",
        path_to_quant_table="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos_quant.csv",
        ionization_mode="pos"
    )
    db_loader = DBLoader(configuration=config.ms_config, logger=logger)
    enhancer = MS1Enhancer(configuration=config.ms_config, logger=logger, db_loader=db_loader)
    # enhancer.initialize_adducts(enhancer.initialize_lotus_objects())
    enhancer.enhance(analysis.spectra)
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

