"""Submodule for the ISDB enhancer."""

import logging
from time import time
from logging import Logger
from typing import Optional

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
from enpkg.monolith.utils import binary_search_by_key, log_virtual_memory
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.loaders.database_manager import DatabaseManager
from enpkg.monolith.loaders.lotus_store import LotusStore


class Ms2Enhancer(Enhancer):
    """Enhancer that adds ISDB information to the analysis."""

    def __init__(
        self,
        configuration: MSEnhancerConfig,
        logger: Logger,
        db_loader: DBLoader,
        lotus_store: LotusStore,
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
        if not isinstance(lotus_store, LotusStore):
            raise TypeError(f"Expected lotus_store of type LotusStore, got {type(lotus_store)}")
        self.configuration = configuration
        self.logger = logger
        self.db_loader = db_loader
        self.lotus_store = lotus_store
        # Lotus list + library-spectrum linking are built lazily on first enhance().
        # In batch mode this means only the first experiment pays the build cost;
        # subsequent ones reuse the same list (the full compound set is identical
        # across experiments).
        self.lotus_objects: Optional[list[Lotus]] = None

        self.logger.info("Loading Databases")
        # Taxonomy access is fully owned by LotusStore now; DBLoader is only
        # around for the spectral DB.
        start = time()
        self.db_loader.load_spectral_databases(mode="pos") # TODO: add mode param to config
        self.logger.debug("Spectral databases loaded in %.2f seconds", time() - start)

        # TODO: Could be put elsewhere
        if not isinstance(self.db_loader.spectral_db, list):
            raise TypeError(f"Expected spectral_db to be a list, got {type(self.db_loader.spectral_db)}")
        if not all(isinstance(spectrum, Spectrum) for spectrum in self.db_loader.spectral_db):
            raise TypeError("Expected all entries in spectral_db to be of type matchms.Spectrum")
        if not all(spectrum.get("compound_name") is not None for spectrum in self.db_loader.spectral_db[:10]):
            raise ValueError("Expected all spectra in spectral_db to have 'compound_name' metadata for short inchikey matching")

        self.logger.info("ISDB Enhancer initialized successfully")
        log_virtual_memory(self.logger, "MS2 init done")

    def _ensure_lotus_objects(self) -> None:
        """Build the sorted Lotus list and link library spectra on first use.

        Deferred from __init__ so the (expensive) full-compound materialisation
        only happens if enhance() is actually called. Idempotent: subsequent
        calls short-circuit on the cached self.lotus_objects.
        """
        if self.lotus_objects is not None:
            return

        self.logger.info(
            "Converting Taxonomical Database metadata to Lotus objects (via LotusStore)"
        )
        start = time()
        self.lotus_objects = self.lotus_store.all_sorted_by_short_inchikey()
        self.logger.info(
            "Built %d Lotus objects in %.2f seconds",
            len(self.lotus_objects), time() - start,
        )
        log_virtual_memory(
            self.logger, f"MS2 lotus_objects built (N={len(self.lotus_objects)})"
        )

        self.logger.info("Adding Lotus entries to spectral database")
        start = time()
        self._link_lotus_to_spectra()
        self.logger.debug(
            "Added Lotus entries to spectral database in %.2f seconds", time() - start
        )
        log_virtual_memory(self.logger, "MS2 lotus linked")

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "ISDB Enhancer"
    
    def _link_lotus_to_spectra(self) -> None:
        """Link Lotus entries to spectral database entries by short inchikey.

        For each spectrum, finds all Lotus entries with matching short inchikey
        using binary search and attaches them as metadata.
        """
        assert self.lotus_objects is not None

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

    def enhance(self, spectrum_list: list[AnnotatedSpectrum], chunk_size: int = 1000) -> list[AnnotatedSpectrum]:
        """Adds ISDB information to the analysis."""

        log_virtual_memory(self.logger, "MS2 enhance start")
        # First call in a batch triggers the expensive LotusStore fetch + library linking.
        self._ensure_lotus_objects()

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

        log_virtual_memory(self.logger, "MS2 enhance end")
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
        path_to_spectra="/home/llegregam/git_projects/enpkg_full/gui_workspace/input/actea_EtOAc-1_pos.mgf",
        path_to_metadata="/home/llegregam/git_projects/enpkg_full/gui_workspace/input/qualome_metadata.txt",
        path_to_quant_table="/home/llegregam/git_projects/enpkg_full/gui_workspace/input/actea_EtOAc-1_pos_quant.csv",
        ionization_mode="pos"
    )
    logger.info(f"Loading enhancer from file")
    start = time()
    db_loader = DBLoader(configuration=config.ms_config, logger=logger)
    lotus_store = LotusStore(
        duckdb_path=config.ms_config.downloader_params.duckdb_path, logger=logger,
    )
    enhancer = Ms2Enhancer(
        configuration=config.ms_config, logger=logger,
        db_loader=db_loader, lotus_store=lotus_store,
    )
    logger.info(f"Enhancer loaded in {time() - start:.2f} seconds")
    start = time()
    enhancer.enhance(analysis.spectra)
    logger.info(f"Enhanced analysis in {time() - start:.2f} seconds")