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
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.enhancers.adducts import POSITIVE_RECIPES, NEGATIVE_RECIPES
from enpkg.monolith.data.ms1_data_classes.adduct_class import ADDUCT_MASSES
from enpkg.monolith.loaders.database_manager import DatabaseManager


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

    def initialize_lotus_objects(
        self,
        spectrum_list: Optional[list] = None,
    ) -> list[list[Lotus]]:
        """
        Initialize the LOTUS objects and group them by their molecular formula.

        When a DuckDB database is configured and ``spectrum_list`` is provided,
        only compounds whose exact mass falls within the reachable range of the
        spectra's precursor m/z values are loaded (on-demand path). This can
        dramatically reduce the number of Lotus objects constructed when the
        sample covers only a fraction of the full LOTUS chemical space.

        Falls back to loading all compounds from the pre-loaded DataFrames when
        no DuckDB path is configured or no spectrum_list is given.

        Parameters
        ----------
        spectrum_list:
            Optional list of AnnotatedSpectrum objects. Used to compute the
            on-demand mass window when DuckDB is available.

        Returns
        -------
        List of lists of LOTUS objects, where each inner list contains LOTUS
        entries with the same molecular formula.
        """
        duckdb_path = self.db_loader.configuration.downloader_params.duckdb_path
        if duckdb_path and spectrum_list is not None:
            return self._initialize_lotus_objects_from_duckdb(spectrum_list, duckdb_path)
        return self._initialize_lotus_objects_from_dataframes()

    def _initialize_lotus_objects_from_duckdb(
        self,
        spectrum_list: list,
        duckdb_path: str,
    ) -> list[list[Lotus]]:
        """
        On-demand Lotus initialisation using DuckDB.

        Derives the exact-mass window reachable from the spectra via all
        adduct recipes, queries only those compounds, and constructs Lotus
        objects from the JOIN result (no separate dict lookups needed).
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
            f"On-demand DuckDB query: exact_mass ∈ [{global_min:.4f}, {global_max:.4f}] "
            f"Da (from {len(precursor_mzs)} spectra × {len(recipes)} recipes)"
        )
        start = time()
        with DatabaseManager(duckdb_path, read_only=True) as db:
            df = db._conn.execute("""
                SELECT c.structure_wikidata, c.structure_inchikey, c.structure_inchi,
                       c.structure_smiles, c.structure_molecular_formula,
                       c.structure_exact_mass, c.structure_xlogp,
                       c."structure_smiles_2D", c.structure_cid,
                       c."structure_nameIupac", c."structure_nameTraditional",
                       c.structure_stereocenters_total,
                       c.structure_stereocenters_unspecified,
                       c.structure_taxonomy_classyfire_chemontid,
                       c.structure_taxonomy_classyfire_01kingdom,
                       c.structure_taxonomy_classyfire_02superclass,
                       c.structure_taxonomy_classyfire_03class,
                       c.structure_taxonomy_classyfire_04directparent,
                       c.organism_wikidata, c.organism_name,
                       c.organism_taxonomy_gbifid, c.organism_taxonomy_ncbiid,
                       c.organism_taxonomy_ottid,
                       c.organism_taxonomy_01domain, c.organism_taxonomy_02kingdom,
                       c.organism_taxonomy_03phylum, c.organism_taxonomy_04class,
                       c.organism_taxonomy_05order, c.organism_taxonomy_06family,
                       c.organism_taxonomy_07tribe, c.organism_taxonomy_08genus,
                       c.organism_taxonomy_09species, c.organism_taxonomy_10varietas,
                       c.reference_wikidata, c.reference_doi, c.manual_validation,
                       n.pathways, n.superclasses, n.classes
                FROM compounds c
                LEFT JOIN npc_classifications n USING (structure_smiles)
                WHERE c.structure_exact_mass BETWEEN ? AND ?
            """, [global_min, global_max]).pl()

        self.logger.debug(
            f"DuckDB returned {len(df):,} compounds in {time() - start:.2f}s "
            f"(out of full DB)"
        )
        if df.is_empty():
            self.logger.warning("DuckDB on-demand query returned 0 compounds; check mass tolerance")
            return []

        return self._build_lotus_groups_from_df(df)

    def _initialize_lotus_objects_from_dataframes(self) -> list[list[Lotus]]:
        """
        Lotus initialisation from the pre-loaded Polars DataFrames (original path).
        """
        import polars as pl

        # Initialize class-level column mappings for efficient Lotus object creation
        # This allows Lotus.from_polars_row to know which index corresponds to which field
        Lotus.setup_lotus_columns(list(self.db_loader.lotus_metadata.columns))
        n_compound_cols = len(self.db_loader.lotus_metadata.columns)

        # Collapse score columns + join onto metadata (same pattern as ms2 DataFrame path)
        start = time()
        pw_cols = self.db_loader.lotus_metadata_pathways.columns[1:]
        sc_cols = self.db_loader.lotus_metadata_superclasses.columns[1:]
        cl_cols = self.db_loader.lotus_metadata_classes.columns[1:]
        merged = (
            self.db_loader.lotus_metadata
            .join(
                self.db_loader.lotus_metadata_pathways.select(
                    "structure_smiles", pl.concat_list(pw_cols).alias("pathways")
                ),
                on="structure_smiles", how="left",
            )
            .join(
                self.db_loader.lotus_metadata_superclasses.select(
                    "structure_smiles", pl.concat_list(sc_cols).alias("superclasses")
                ),
                on="structure_smiles", how="left",
            )
            .join(
                self.db_loader.lotus_metadata_classes.select(
                    "structure_smiles", pl.concat_list(cl_cols).alias("classes")
                ),
                on="structure_smiles", how="left",
            )
        )
        self.logger.debug(f"Built merged DataFrame in {time() - start:.2f}s")

        # Build nested structure: group all LOTUS entries by molecular formula
        # Outer loop: iterate over groups (one per unique molecular formula)
        # Inner loop: iterate over rows within each group (individual compounds/isomers)
        start = time()
        lotus_grouped: list[list[Lotus]] = [
            [
                Lotus.from_polars_row(
                    list(row[:n_compound_cols]),
                    pathways=np.array(row[n_compound_cols]) if row[n_compound_cols] is not None else np.array([]),
                    superclasses=np.array(row[n_compound_cols + 1]) if row[n_compound_cols + 1] is not None else np.array([]),
                    classes=np.array(row[n_compound_cols + 2]) if row[n_compound_cols + 2] is not None else np.array([]),
                )
                for row in group.iter_rows()
            ]
            for _, group in tqdm(
                merged.group_by("structure_molecular_formula"),
                desc="Initializing LOTUS objects",
                dynamic_ncols=True,
                leave=False,
            )
        ]
        self.logger.debug(f"Built {len(lotus_grouped)} LOTUS groups in {time() - start:.2f}s")
        return lotus_grouped

    def _build_lotus_groups_from_df(self, df) -> list[list[Lotus]]:
        """
        Build Lotus objects grouped by molecular formula from a Polars DataFrame
        returned by a DuckDB query (includes 'pathways', 'superclasses', 'classes' LIST columns).
        """
        import polars as pl

        # The compound columns are everything except the three appended LIST columns
        list_col_set = {"pathways", "superclasses", "classes"}
        compound_cols = [c for c in df.columns if c not in list_col_set]
        Lotus.setup_lotus_columns(compound_cols)

        smiles_idx = compound_cols.index("structure_smiles")
        formula_idx = compound_cols.index("structure_molecular_formula")

        start = time()
        # Partition by formula in Python (Polars group_by on a subset of columns)
        formula_col = df["structure_molecular_formula"]
        unique_formulas = formula_col.unique().to_list()

        lotus_groups: list[list[Lotus]] = []
        for formula in tqdm(unique_formulas, desc="Initializing LOTUS objects", dynamic_ncols=True, leave=False):
            group_mask = formula_col == formula
            group_df = df.filter(group_mask)
            group: list[Lotus] = []
            for row in group_df.iter_rows():
                # row has compound columns + pathways, superclasses, classes at the end
                compound_row = list(row[:len(compound_cols)])
                pw = np.array(row[len(compound_cols)]) if row[len(compound_cols)] is not None else np.array([])
                sc = np.array(row[len(compound_cols) + 1]) if row[len(compound_cols) + 1] is not None else np.array([])
                cl = np.array(row[len(compound_cols) + 2]) if row[len(compound_cols) + 2] is not None else np.array([])
                group.append(Lotus.from_polars_row(compound_row, pathways=pw, superclasses=sc, classes=cl))
            if group:
                lotus_groups.append(group)

        self.logger.debug(f"Built {len(lotus_groups)} LOTUS groups in {time() - start:.2f}s")
        return lotus_groups


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
            lotus_grouped_by_structure_molecular_formula = self.initialize_lotus_objects(
                spectrum_list=spectrum_list
            )
            self.logger.info(f"Finished initializing LOTUS objects in {time() - start:.2f} seconds")
            start = time()
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

