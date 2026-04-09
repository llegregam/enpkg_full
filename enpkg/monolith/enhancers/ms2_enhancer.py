"""Submodule for the ISDB enhancer."""

import gc
import logging
from time import time
from typing import Optional
from logging import Logger
import matchms
import pandas as pd
import numpy as np
from tqdm.auto import tqdm, trange
from tqdm.contrib import tzip

from matchms import calculate_scores
from matchms.similarity import PrecursorMzMatch
from matchms.similarity import CosineGreedy, CosineHungarian
from matchms import Spectrum

from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig, SpectralMatchParams
from enpkg.monolith.data.chemical_annotation import MS2ChemicalAnnotation
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.utils import binary_search_by_key
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.loaders.database_manager import DatabaseManager


class Ms2Enhancer(Enhancer):
    """Enhancer that adds ISDB information to the analysis."""

    def __init__(
        self, configuration: MSEnhancerConfig, logger: Logger, db_loader: DBLoader
    ):
        """Initializes the enhancer."""
        
        if not isinstance(configuration, MSEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type ISDBEnhancerConfig, got {type(configuration)}"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"Expected logger of type logging.Logger, got {type(logger)}")
        if not isinstance(db_loader, DBLoader):
            raise TypeError(f"Expected db_loader of type DBLoader, got {type(db_loader)}")
        self.configuration = configuration
        self.logger = logger
        self.db_loader = db_loader
        
        self.logger.info("Loading Databases")
        start = time()
        self.db_loader.load_taxonomical_databases()
        self.logger.debug("Taxonomical databases loaded in %.2f seconds", time() - start)
        start = time()
        self.db_loader.load_spectral_databases(mode="pos") # TODO: add mode param to config
        self.logger.debug("Spectral databases loaded in %.2f seconds", time() - start)
        
        self.logger.info(
            "Converting Taxonomical Database metadata DataFrame to Lotus objects"
        )
        start = time()
        self._initialize_lotus_objects()
        self.logger.info(
            "Converted Taxonomical Database metadata DataFrame to Lotus objects in %.2f seconds",
            time() - start,
        )
        # Liberate memory by deleting the original dataframes and keeping only the Lotus objects and spectral database in memory
        # (Can't be bothered to wait for the garbage collector)
        del self.db_loader.lotus_metadata, self.db_loader.lotus_metadata_pathways, 
        self.db_loader.lotus_metadata_superclasses, self.db_loader.lotus_metadata_classes
        gc.collect()

        # TODO: Could be put elsewhere
        if not isinstance(self.db_loader.spectral_db, list):
            raise TypeError(f"Expected spectral_db to be a list, got {type(self.db_loader.spectral_db)}")
        if not all(isinstance(spectrum, Spectrum) for spectrum in self.db_loader.spectral_db):
            raise TypeError("Expected all entries in spectral_db to be of type matchms.Spectrum")
        if not all(spectrum.get("compound_name") is not None for spectrum in self.db_loader.spectral_db[:10]):
            raise ValueError("Expected all spectra in spectral_db to have 'compound_name' metadata for short inchikey matching")

        self.logger.info("Adding Lotus entries to spectral database")
        start = time()
        self._link_lotus_to_spectra()
        self.logger.debug(
            "Added Lotus entries to spectral database in %.2f seconds", time() - start
        )

        self.logger.info("ISDB Enhancer initialized successfully")

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "ISDB Enhancer"
    
    def _link_lotus_to_spectra(self) -> None:
        """Link Lotus entries to spectral database entries by short inchikey.
        
        For each spectrum, finds all Lotus entries with matching short inchikey
        using binary search and attaches them as metadata.
        """

        start = time()
        for spectrum in tqdm(
            self.db_loader.spectral_db,
            desc="Adding Lotus entries to spectral database",
            dynamic_ncols=True,
            leave=False,
        ):
            spectrum_short_inchikey = spectrum.get("compound_name")
            (found, smallest_idx) = binary_search_by_key(
                key=spectrum_short_inchikey,
                array=self.lotus_objects,
                key_func=lambda x: x.short_inchikey,
            )

            if not found:
                continue

            # Since we may have landed exactly in the middle of an array of short inchikeys
            # with the same value, we need to identify the smallest index of the slice of
            # short inchikeys with the same value.
            while (
                smallest_idx > 0
                and self.lotus_objects[smallest_idx - 1].short_inchikey
                == spectrum_short_inchikey
            ):
                smallest_idx -= 1

            largest_idx = smallest_idx

            while (
                largest_idx < len(self.lotus_objects)
                and self.lotus_objects[largest_idx].short_inchikey == spectrum_short_inchikey
            ):
                largest_idx += 1

            spectrum.set("lotus_entries", self.lotus_objects[smallest_idx:largest_idx])

        self.logger.debug(f"Linked lotus to spectra in {time() - start:.2f} seconds")

    
    def _initialize_lotus_objects(self) -> None:
        """Initializes the Lotus objects, sorted by short_inchikey for binary search.

        Uses DuckDB's ORDER BY short_inchikey when a duckdb_path is configured,
        avoiding the Python-side sorted() call over the full list.
        """
        duckdb_path = self.db_loader.configuration.downloader_params.duckdb_path
        if duckdb_path:
            self._initialize_lotus_objects_from_duckdb(duckdb_path)
        else:
            self._initialize_lotus_objects_from_dataframes()

    def _initialize_lotus_objects_from_duckdb(self, duckdb_path: str) -> None:
        """Load Lotus objects pre-sorted by short_inchikey from DuckDB."""


        self.logger.debug(f"Loading Lotus objects from DuckDB (sorted): {duckdb_path}")
        start = time()
        with DatabaseManager(duckdb_path, read_only=True) as db:
            df = db.get_compounds_sorted_by_short_inchikey()

        self.logger.debug(f"DuckDB returned {len(df):,} compounds in {time() - start:.2f}s")

        list_col_set = {"pathways", "superclasses", "classes"}
        compound_cols = [c for c in df.columns if c not in list_col_set]
        Lotus.setup_lotus_columns(compound_cols)

        n_compound_cols = len(compound_cols)
        start = time()
        self.lotus_objects: list[Lotus] = [
            Lotus.from_polars_row(
                list(row[:n_compound_cols]),
                pathways=np.array(row[n_compound_cols]) if row[n_compound_cols] is not None else np.array([]),
                superclasses=np.array(row[n_compound_cols + 1]) if row[n_compound_cols + 1] is not None else np.array([]),
                classes=np.array(row[n_compound_cols + 2]) if row[n_compound_cols + 2] is not None else np.array([]),
            )
            for row in tqdm(df.iter_rows(), total=len(df), desc="Creating Lotus objects", leave=False, dynamic_ncols=True)
        ]
        self.logger.debug(
            "Created %d Lotus objects from DuckDB in %.2f seconds",
            len(self.lotus_objects), time() - start,
        )
        # Already sorted by DuckDB ORDER BY short_inchikey — no Python sort needed.

    def _initialize_lotus_objects_from_dataframes(self) -> None:
        """Initializes the Lotus objects from the pre-loaded metadata DataFrames (original path)."""
        import polars as pl

        Lotus.setup_lotus_columns(list(self.db_loader.lotus_metadata.columns))
        n_compound_cols = len(self.db_loader.lotus_metadata.columns)
        self.logger.debug(f"Lotus columns: {Lotus._columns}")

        # Collapse each classification DataFrame's score columns into a single list column,
        # then join all three onto the metadata in one pass — mirrors the DuckDB path structure.
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

        start = time()
        self.lotus_objects: list[Lotus] = [
            Lotus.from_polars_row(
                list(row[:n_compound_cols]),
                pathways=np.array(row[n_compound_cols]) if row[n_compound_cols] is not None else np.array([]),
                superclasses=np.array(row[n_compound_cols + 1]) if row[n_compound_cols + 1] is not None else np.array([]),
                classes=np.array(row[n_compound_cols + 2]) if row[n_compound_cols + 2] is not None else np.array([]),
            )
            for row in tqdm(merged.iter_rows(), total=len(merged), desc="Creating Lotus objects", leave=False, dynamic_ncols=True)
        ]
        self.logger.debug(
            "Converted Taxonomical Database metadata DataFrame to Lotus objects in %.2f seconds",
            time() - start,
        )

        self.logger.debug("Sorting Lotus entries by short inchikey")
        start = time()
        self.lotus_objects = sorted(self.lotus_objects, key=lambda x: x.short_inchikey)
        self.logger.debug(
            "Sorted Lotus entries by short inchikey in %.2f seconds", time() - start
        )

    def enhance(self, spectrum_list: list[AnnotatedSpectrum], chunk_size: int = 1000) -> list[AnnotatedSpectrum]:
        """Adds ISDB information to the analysis."""

        number_of_spectra = len(spectrum_list)
        self.logger.info(f"Running MS2 enrichment process on {number_of_spectra} spectra")
        similarity_score = PrecursorMzMatch(
            tolerance=self.configuration.spectral_match_params.parent_mz_tol,
            tolerance_type="Dalton",
        )
        match self.configuration.spectral_match_params.method:
            case "cosine_greedy":
                similarity_function = CosineGreedy(
                    tolerance=self.configuration.spectral_match_params.msms_mz_tol
                )
            case "cosine_hungarian":
                similarity_function = CosineHungarian(
                    tolerance=self.configuration.spectral_match_params.msms_mz_tol
                    # TODO: Consider adding mz_power and intensity_power parameters
                )
        self.logger.debug(
            f"similarity_score: {similarity_score}\n{self.configuration.spectral_match_params.method}: {similarity_function}"
        )

        for min_range in trange(
            0,
            number_of_spectra,
            chunk_size,
            desc="Spectral matching",
            leave=False,
            dynamic_ncols=True,
        ):
            spectra_chunk: list[AnnotatedSpectrum] = spectrum_list[
                min_range : min_range + chunk_size
            ]

            # We start by matching the precursor m/z of the spectra in the analysis against the 
        # precursor m/z of the spectra in the database with a specific parent_tol  (e.g., 0.01 Da).
            cosine_similarities_with_database: matchms.Scores = calculate_scores(
                references=spectra_chunk,
                queries=self.db_loader.spectral_db,
                similarity_function=similarity_score,
            )
            
            # Reference indices are the indices of the spectra in the input data (i.e., the spectra in the analysis)
            reference_indices: np.ndarray = cosine_similarities_with_database.scores[:, :][0]

            # Query indices are the indices of the spectra in the database. 
            query_indices: np.ndarray = cosine_similarities_with_database.scores[:, :][1]
            
            # Get the cosine similarity scores of all matches between reference and query spectra 
            for ref_idx, query_idx in tzip(
                reference_indices,
                query_indices,
                desc="Processing chunk similarities",
                leave=False,
            ):
                msms_score, n_matches = similarity_function.pair(
                    spectra_chunk[ref_idx], self.db_loader.spectral_db[query_idx]
                )[()] # Numpy indexing to extract a "scalar" (here a tuple (score, n_matches)) value from a 0-dim array
                if (
                    msms_score > self.configuration.spectral_match_params.min_score
                    and 
                    n_matches > self.configuration.spectral_match_params.min_peaks
                ):
                    lotus_entries: list[Lotus] = self.db_loader.spectral_db[query_idx].get("lotus_entries")
                    spectra_chunk[ref_idx].add_ms2_annotation(
                        MS2ChemicalAnnotation(
                            source="Lotus",
                            queried_against="ISDB", # TODO: Create versioning system for databases and include version in the annotation
                            scores={
                                self.configuration.spectral_match_params.method: {
                                    "value": msms_score,
                                    "n_matches": n_matches
                                }
                            },
                            lotus_entries=lotus_entries,
                        )
                    )

        # Debug: Sample 5 random spectra to show annotation statistics
        import random
        sample_size = min(5, len(spectrum_list))
        sampled_spectra = random.sample(spectrum_list, k=sample_size)
        for spectrum in sampled_spectra:
            n_annotations = len(spectrum.ms2_annotations) if spectrum.ms2_annotations else 0
            self.logger.debug(f"Spectrum {spectrum.feature_id}: {n_annotations} MS2 annotations")
            if n_annotations > 0:
                # Show first 3 annotations as sample
                for i, annotation in enumerate(spectrum.ms2_annotations[:3]):
                    self.logger.debug(
                        f"  Annotation {i+1}: source={annotation.source}, "
                        f"queried_against={annotation.queried_against}, "
                        f"scores={annotation.scores}, "
                        f"n_lotus_entries={len(annotation.lotus_entries) if annotation.lotus_entries else 0}"
                    )

        # TODO: Decide if analysis should be modified in place or if we should return a new enriched analysis object
        return spectrum_list


if __name__ == "__main__":

    from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
    from enpkg.monolith.pipeline.test_pol import load_config

    logger = logging.getLogger("DBLoader")
    logging.basicConfig(level=logging.DEBUG)
    # Silence numba - only show warnings and above
    logging.getLogger("numba").setLevel(logging.WARNING)
    config = load_config()
    config.ms_config.spectral_match_params.method = "cosine_hungarian" # or "hungarian"
    # )
    analysis = AnalysisLoader.from_files(
        path_to_spectra="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos.mgf",
        path_to_metadata="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/metadata/metadata.tsv",
        path_to_quant_table="/home/llegregam/git_projects/enpkg_full/data/input/enpkg_toy_dataset/msdata/processed/VGF151_E05_pos_quant.csv",
        ionization_mode="pos"
    )
    logger.info(f"Loading enhancer from file")
    start = time()
    enhancer = Ms2Enhancer(configuration=config.ms_config, logger=logger, db_loader=DBLoader(configuration=config.ms_config, logger=logger))
    logger.info(f"Enhancer loaded in {time() - start:.2f} seconds")
    start = time()
    enhancer.enhance(analysis.spectra)
    logger.info(f"Enhanced analysis in {time() - start:.2f} seconds")