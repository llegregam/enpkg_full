"""Submodule for the ISDB enhancer."""

from itertools import groupby
from logging import Logger
from time import time
from typing import Optional

import matchms
import numpy as np
from matchms import Spectrum, calculate_scores
from matchms.similarity import CosineGreedy, CosineHungarian, PrecursorMzMatch
from tqdm.auto import tqdm, trange
from tqdm.contrib import tzip

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.data.chemical_annotation import AnnotationOrganism, MS2ChemicalAnnotation
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.loaders.lotus_store import LotusStore


class Ms2Enhancer(Enhancer):
    """Enhancer that adds MS2 information to the analysis."""

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
                f"Expected configuration of type MSEnhancerConfig, got {type(configuration)}"
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
        # around for the spectral DB. Load the library matching the configured
        # polarity so negative-mode runs query the negative library.
        start = time()
        self.db_loader.load_spectral_databases(
            mode=self.configuration.general_params.polarity
        )
        self.logger.debug("Spectral databases loaded in %.2f seconds", time() - start)

        # TODO: Could be put elsewhere
        if not isinstance(self.db_loader.spectral_db, list):
            raise TypeError(f"Expected spectral_db to be a list, got {type(self.db_loader.spectral_db)}")
        if not all(isinstance(spectrum, Spectrum) for spectrum in self.db_loader.spectral_db):
            raise TypeError("Expected all entries in spectral_db to be of type matchms.Spectrum")
        if not all(spectrum.get("compound_name") is not None for spectrum in self.db_loader.spectral_db[:10]):
            raise ValueError("Expected all spectra in spectral_db to have 'compound_name' metadata for short inchikey matching")

        self.logger.info("MS2 Enhancer initialized successfully")

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

        self.logger.info("Adding Lotus entries to spectral database")
        start = time()
        self._link_lotus_to_spectra()
        self.logger.debug(
            "Added Lotus entries to spectral database in %.2f seconds", time() - start
        )

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS2 Enhancer"

    def _link_lotus_to_spectra(self) -> None:
        """Link Lotus entries to spectral database entries by short inchikey.

        Builds a dict short_inchikey -> [Lotus, ...] in one pass over the
        pre-sorted self.lotus_objects, then attaches matching slices to each
        spectrum via O(1) lookups.
        """
        assert self.lotus_objects is not None

        start = time()
        lotus_by_short_inchikey: dict[str, list[Lotus]] = {
            key: list(group)
            for key, group in groupby(self.lotus_objects, key=lambda x: x.short_inchikey)
        }

        for spectrum in tqdm(
            self.db_loader.spectral_db,
            desc="Adding Lotus entries to spectral database",
            dynamic_ncols=True,
            leave=False,
        ):
            compound_name = spectrum.get("compound_name")
            if compound_name is None:
                self.logger.warning(
                    "Spectrum %s has no compound_name metadata; skipping Lotus linking",
                    spectrum.get("spectrum_id", "unknown"),
                )
                continue
            entries = lotus_by_short_inchikey.get(compound_name)
            spectrum.set("lotus_entries", entries)

        self.logger.debug(f"Linked lotus to spectra in {time() - start:.2f} seconds")

    def enhance(self, analysis: Analysis, chunk_size: int = 1000) -> Analysis:
        """Add MS2 chemical annotations to each spectrum via two-stage matching.

        Stage 1 — ``precursor_filter`` (PrecursorMzMatch): cheap pre-filter run
        in batch via ``calculate_scores``. For every (reference, query) pair it
        only checks whether the precursor m/z agree within ``parent_mz_tol``;
        no MS/MS comparison happens here.

        Stage 2 — ``cosine_similarity`` (CosineGreedy or CosineHungarian): the
        actual MS/MS cosine, computed via ``.pair()`` only on pairs that
        survived stage 1. This is the dominant cost.

        Spectra are processed in chunks of ``chunk_size`` references against
        the full library so memory stays bounded.

        Annotations passing both ``min_score`` and ``min_peaks`` are appended
        in place to ``spectrum.ms2_annotations`` along with the matched
        library spectrum's ``lotus_entries``.
        """

        spectrum_list: tuple[AnnotatedSpectrum] = analysis.spectra

        # First call in a batch triggers the expensive LotusStore fetch + library linking.
        self._ensure_lotus_objects()

        number_of_spectra = len(spectrum_list)
        self.logger.info(f"Running MS2 enrichment process on {number_of_spectra} spectra")
        # Stage-1 filter: cheap precursor m/z agreement test (returns bool per pair).
        precursor_filter = PrecursorMzMatch(
            tolerance=self.configuration.spectral_match_params.parent_mz_tol,
            tolerance_type="Dalton",
        )
        # Stage-2 scorer: actual MS/MS cosine, only run on pairs that passed stage 1.
        match self.configuration.spectral_match_params.method:
            case "cosine_greedy":
                cosine_similarity = CosineGreedy(
                    tolerance=self.configuration.spectral_match_params.msms_mz_tol
                )
            case "cosine_hungarian":
                cosine_similarity = CosineHungarian(
                    tolerance=self.configuration.spectral_match_params.msms_mz_tol
                    # TODO: Consider adding mz_power and intensity_power parameters
                )
            case _:
                raise ValueError(
                    f"Unknown spectral match method: {self.configuration.spectral_match_params.method!r}"
                )
        self.logger.debug(
            f"precursor_filter: {precursor_filter}\n{self.configuration.spectral_match_params.method}: {cosine_similarity}"
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

            # Stage 1: filter (ref, query) pairs by precursor m/z within parent_tol.
            # The Scores object holds precursor matches, NOT cosine scores —
            # the cosine is computed below in stage 2 on the survivors only.
            precursor_matches: matchms.Scores = calculate_scores(
                references=spectra_chunk,
                queries=self.db_loader.spectral_db,
                similarity_function=precursor_filter,
            )

            # Reference indices are the indices of the spectra in the input data (i.e., the spectra in the analysis)
            reference_indices: np.ndarray = precursor_matches.scores[:, :][0]

            # Query indices are the indices of the spectra in the database.
            query_indices: np.ndarray = precursor_matches.scores[:, :][1]

            # Stage 2: compute cosine on each precursor-matched pair.
            for ref_idx, query_idx in tzip(
                reference_indices,
                query_indices,
                desc="Processing chunk similarities",
                leave=False,
            ):
                msms_score, n_matches = cosine_similarity.pair(
                    spectra_chunk[ref_idx], self.db_loader.spectral_db[query_idx]
                )[()] # Numpy indexing to extract a "scalar" (here a tuple (score, n_matches)) value from a 0-dim array
                # min_peaks is an inclusive minimum (>=); min_score stays a strict
                # lower bound (a match must beat the floor, not merely equal it).
                if (
                    msms_score > self.configuration.spectral_match_params.min_score
                    and
                    n_matches >= self.configuration.spectral_match_params.min_peaks
                ):
                    lotus_entries: list[Lotus] = self.db_loader.spectral_db[query_idx].get("lotus_entries")
                    # No taxonomical-DB structure for this library hit -> no
                    # InChIKey / classification arrays / organisms to keep.
                    if not lotus_entries:
                        continue
                    representative: Lotus = lotus_entries[0]
                    organisms = [
                        AnnotationOrganism(
                            name=entry.organism_name,
                            wikidata=entry.organism_wikidata,
                            ott_id=entry.organism_taxonomy_ottid,
                            domain=entry.domain,
                            kingdom=entry.kingdom,
                            phylum=entry.phylum,
                            klass=entry.klass,
                            order=entry.order,
                            family=entry.family,
                            genus=entry.genus,
                            species=entry.species,
                        )
                        for entry in lotus_entries
                    ]
                    spectra_chunk[ref_idx].add_ms2_annotation(
                        MS2ChemicalAnnotation(
                            source="Lotus",
                            queried_against="ISDB", # TODO: Create versioning system for databases and include version in the annotation
                            short_inchikey=representative.short_inchikey,
                            score=float(msms_score),
                            n_matched_peaks=int(n_matches),
                            pathway_scores=representative.structure_taxonomy_hammer_pathways,
                            superclass_scores=representative.structure_taxonomy_hammer_superclasses,
                            class_scores=representative.structure_taxonomy_hammer_classes,
                            organisms=organisms,
                        )
                    )

        # Spectra are annotated in place; the same Analysis is returned (uniform
        # enhancer contract).
        return analysis
