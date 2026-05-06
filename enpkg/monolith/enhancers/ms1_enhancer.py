#!/usr/bin/env python3

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
from enpkg.monolith.data.analysis import AnnotatedSpectrum
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.ms1_data_classes import ChemicalAdduct
from enpkg.monolith.utils import binary_search_by_key, label_propagation_algorithm
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.enhancers.adducts import POSITIVE_RECIPES, NEGATIVE_RECIPES
from enpkg.monolith.data.ms1_data_classes.adduct_class import ADDUCT_MASSES


class MS1Enhancer(Enhancer):
    """Enhancer that adds MS1 information to the analysis."""

    def __init__(self, configuration: MSEnhancerConfig, logger: Logger, lotus_store: LotusStore):
        """Initializes the enhancer."""

        if not isinstance(configuration, MSEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type MSEnhancerConfig, got {type(configuration)}"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"Expected logger of type logging.Logger, got {type(logger)}")
        if not isinstance(lotus_store, LotusStore):
            raise TypeError(f"Expected lotus_store of type LotusStore, got {type(lotus_store)}")

        self.configuration = configuration
        self.logger = logger
        # LotusStore owns compound access; DBLoader is no longer needed here
        # (MS1 doesn't touch the spectral library).
        self.lotus_store = lotus_store

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

    def initialize_lotus_objects(
        self,
        spectrum_list: list,
    ) -> list[list[Lotus]]:
        """
        Compute the exact-mass window reachable from the spectra's precursor
        m/z values (across every ionization recipe) and delegate the grouped
        Lotus build to the LotusStore.

        Parameters
        ----------
        spectrum_list:
            List of AnnotatedSpectrum objects whose precursor m/z values drive
            the mass-window filter.

        Returns
        -------
        List of lists of LOTUS objects, where each inner list contains LOTUS
        entries with the same molecular formula.
        """
        recipes = (
            POSITIVE_RECIPES
            if self.configuration.general_params.polarity == "pos"
            else NEGATIVE_RECIPES
        )
        tol = self.configuration.spectral_match_params.parent_mz_tol
        precursor_mzs = [s.precursor_mz for s in spectrum_list]

        # Compute the exact-mass window reachable from all spectra + all recipes.
        # For each recipe: exact_mass = (precursor_mz * charge - adduct_sum) / multimer_factor
        # We accumulate the global min/max across all (recipe, spectrum) pairs.
        global_min = float("inf")
        global_max = float("-inf")
        for recipe in recipes:
            adduct_sum = sum(
                ADDUCT_MASSES[k] * v for k, v in recipe.ingredients.items()
            )
            for mz in precursor_mzs:
                em_min = ((mz - tol) * recipe.charge - adduct_sum) / recipe.multimer_factor
                em_max = ((mz + tol) * recipe.charge - adduct_sum) / recipe.multimer_factor
                if em_min < global_min:
                    global_min = em_min
                if em_max > global_max:
                    global_max = em_max

        # # Add a small buffer to account for floating-point rounding
        # global_min -= tol
        # global_max += tol

        self.logger.debug(
            f"Mass-window query: exact_mass ∈ [{global_min:.4f}, {global_max:.4f}] "
            f"Da (from {len(precursor_mzs)} spectra × {len(recipes)} recipes)"
        )
        return self.lotus_store.grouped_by_formula_for_mass_range(global_min, global_max)


    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS1 Enhancer"

    def enhance(self, spectrum_list: list[AnnotatedSpectrum]) -> list[AnnotatedSpectrum]:
        """Adds MS1 information to the analysis."""

        self.logger.info("Running enrichment process")
        number_of_spectra = len(spectrum_list)
        self.logger.info(f"Number of spectra to enrich: {number_of_spectra}")
        # Classification counts now come from the LotusStore (DuckDB-resolved at
        # construction time), not from DBLoader DataFrames.
        self._number_of_pathways = self.lotus_store.number_of_pathways
        self._number_of_superclasses = self.lotus_store.number_of_superclasses
        self._number_of_classes = self.lotus_store.number_of_classes

        # Adducts are rebuilt on every enhance() call. The previous hasattr cache
        # was unsafe in batch mode — experiment 2 would reuse experiment 1's
        # mass-windowed adducts even though its spectra cover a different m/z range.
        self.logger.info("Initializing LOTUS objects and adducts")
        start = time()
        lotus_grouped_by_structure_molecular_formula = self.initialize_lotus_objects(
            spectrum_list=spectrum_list
        )
        self.logger.info(f"Finished initializing LOTUS objects in {time() - start:.2f} seconds")
        start = time()
        self._adducts = self.initialize_adducts(lotus_grouped_by_structure_molecular_formula)
        self.logger.info(
            f"Initialized {len(self._adducts)} adducts in {time() - start:.2f} seconds"
        )

        for i, spectrum in tqdm(
            enumerate(spectrum_list),
            leave=False,
            total=number_of_spectra,
            desc="Filtering precursor adducts",
            dynamic_ncols=True,
        ):
            lower_mass_bound = (
                spectrum.precursor_mz
                - self.configuration.spectral_match_params.parent_mz_tol
            )
            upper_mass_bound = (
                spectrum.precursor_mz
                + self.configuration.spectral_match_params.parent_mz_tol
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
    logger = logging.getLogger("root")

    config = load_config()
    logger.info(f"Using configuration:\n{config}")
    # TODO: Think about how ionization mode is set in the pipelines.
    analysis = AnalysisLoader.from_files(
        path_to_spectra="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos.mgf",
        path_to_metadata="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/metadata/metadata.tsv",
        path_to_quant_table="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos_quant.csv",
        ionization_mode="pos"
    )
    lotus_store = LotusStore(
        duckdb_path=config.ms_config.downloader_params.duckdb_path, logger=logger,
    )
    enhancer = MS1Enhancer(
        configuration=config.ms_config, logger=logger, lotus_store=lotus_store,
    )
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

