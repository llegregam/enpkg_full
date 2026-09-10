"""Submodule for the MS2 spectral-matching enhancer."""

from itertools import groupby
from logging import Logger
from time import time
from typing import Optional

from matchms.similarity import CosineGreedy, CosineHungarian
from tqdm.auto import trange
from tqdm.contrib import tzip

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.data.chemical_annotation import AnnotationOrganism, MS2ChemicalAnnotation
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.loaders.spectral_library_store import (
    LibraryCandidate,
    SpectralLibraryStore,
)
from enpkg.monolith.utils.ms2_adduct_gating import select_spectra_for_ms2


class Ms2Enhancer(Enhancer):
    """Enhancer that adds MS2 annotations to the analysis."""

    def __init__(
        self,
        configuration: MSEnhancerConfig,
        logger: Logger,
        library_store: SpectralLibraryStore,
        lotus_store: LotusStore,
    ):
        """Initializes the enhancer."""

        if not isinstance(configuration, MSEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type MSEnhancerConfig, got {type(configuration)}"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"Expected logger of type logging.Logger, got {type(logger)}")
        if not isinstance(library_store, SpectralLibraryStore):
            raise TypeError(
                f"Expected library_store of type SpectralLibraryStore, "
                f"got {type(library_store)}"
            )
        if not isinstance(lotus_store, LotusStore):
            raise TypeError(f"Expected lotus_store of type LotusStore, got {type(lotus_store)}")
        self.configuration = configuration
        self.logger = logger
        self.library_store = library_store
        self.lotus_store = lotus_store
        # The Lotus list is built lazily on first enhance(). In batch mode only the
        # first experiment pays the build cost; later ones reuse it, since the full
        # compound set is identical across experiments.
        self.lotus_objects: Optional[list[Lotus]] = None
        self._lotus_by_short_inchikey: dict[str, list[Lotus]] = {}

        self.logger.info("MS2 Enhancer initialized successfully")

    def _ensure_lotus_objects(self) -> None:
        """Materialise the Lotus list and index it by short InChIKey.

        Deferred from ``__init__`` so the expensive full-compound fetch only
        happens if ``enhance()`` is actually called. Idempotent: later calls
        short-circuit on the cached list.
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

        # groupby needs its input sorted by the grouping key, which is what
        # all_sorted_by_short_inchikey guarantees.
        self._lotus_by_short_inchikey = {
            key: list(group)
            for key, group in groupby(self.lotus_objects, key=lambda x: x.short_inchikey)
        }
        self.logger.debug(
            "Indexed %d distinct short InChIKeys", len(self._lotus_by_short_inchikey)
        )

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS2 Enhancer"

    def _annotate(
        self,
        spectrum: AnnotatedSpectrum,
        candidate: LibraryCandidate,
        library_label: str,
        score: float,
        n_matches: int,
    ) -> bool:
        """Attach one MS2 annotation for a scored candidate, if LOTUS knows it.

        Returns True when an annotation was added. A candidate whose structure is
        absent from LOTUS currently yields nothing: the annotation model carries
        the classification vectors and source organisms that only LOTUS provides.
        """
        # TODO: let MS2ChemicalAnnotation carry classifications from a source other
        # than LOTUS, so a candidate LOTUS does not know is annotated rather than
        # dropped. Three things block that today, and they are separable:
        #
        #  1. The model is LOTUS-shaped. `source` is hardcoded "Lotus";
        #     `pathway_scores` / `superclass_scores` / `class_scores` are the LOTUS
        #     probability vectors; `organisms` has no library equivalent at all;
        #     `short_inchikey` is required, but a library hit is identified by its
        #     own INCHIKEY/SMILES/FORMULA, which are not currently stored anywhere.
        #  2. The types differ. LOTUS gives a score per NPC term over the whole
        #     vocabulary; `candidate.npc_pathway` / `npc_superclass` / `npc_class`
        #     give one label per rank. Reweighting multiplies elementwise against
        #     LOTUS-length vectors, so a library label is only usable once one-hot
        #     encoded into `LotusStore.pathways_col_names` and the two sibling
        #     vocabularies — a different vocabulary misaligns silently.
        #  3. `weights_enhancer` filters on `has_organisms()`, so an annotation with
        #     no source organism is dropped downstream even if it is emitted here.
        #
        # Open question — precedence, once an annotation can have two sources.
        # Identity cannot conflict: the LOTUS entry is looked up *by* the
        # candidate's short InChIKey, so both describe the same structure. The
        # classifications can conflict, and it is not settled which wins when a
        # structure is in LOTUS and the library also carries NPC labels: a LOTUS
        # score vector is strictly more informative than a one-hot, which argues
        # for LOTUS first and the library as fallback, but the library label is the
        # one attached to the spectrum that actually matched. ClassyFire has no
        # LOTUS counterpart and no consumer, so it is additive either way. Record
        # the winner on the annotation (a `classification_source` field) rather
        # than resolving it silently, or the reranking input becomes unattributable.
        if not candidate.short_inchikey:
            return False
        lotus_entries = self._lotus_by_short_inchikey.get(candidate.short_inchikey)
        if not lotus_entries:
            return False

        representative = lotus_entries[0]
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
        spectrum.add_ms2_annotation(
            MS2ChemicalAnnotation(
                source="Lotus",
                queried_against=library_label,
                short_inchikey=representative.short_inchikey,
                score=float(score),
                n_matched_peaks=int(n_matches),
                pathway_scores=representative.structure_taxonomy_hammer_pathways,
                superclass_scores=representative.structure_taxonomy_hammer_superclasses,
                class_scores=representative.structure_taxonomy_hammer_classes,
                organisms=organisms,
            )
        )
        return True

    def enhance(self, analysis: Analysis, chunk_size: int = 1000) -> Analysis:
        """Add MS2 chemical annotations to each spectrum via two-stage matching.

        Stage 1 — precursor m/z agreement — runs in the database: ``candidates_for``
        returns only the library spectra whose precursor is within ``parent_mz_tol``
        of a feature in the current chunk. This is the same predicate matchms'
        ``PrecursorMzMatch(tolerance, "Dalton")`` applies, evaluated where the data
        already lives, so the library is never held in memory in full.

        Stage 2 — the MS/MS cosine (CosineGreedy or CosineHungarian) — is computed
        only on those surviving pairs, and is the dominant cost.

        Features are processed in chunks of ``chunk_size`` so the retrieved
        candidate set stays bounded. Annotations passing both ``min_score`` and
        ``min_peaks`` are appended in place to ``spectrum.ms2_annotations``.
        """
        # Gate on the MS1 adduct-graph roles: spectral libraries are almost all
        # base-ion ([M+H]+/[M-H]-), so resolved non-base adducts are skipped.
        # Falls back to all features (with a warning) if the graph did not run.
        spectrum_list: list[AnnotatedSpectrum] = select_spectra_for_ms2(
            analysis.spectra,
            self.configuration.ms2_adduct_filter,
            self.logger,
        )

        # First call in a batch triggers the expensive LotusStore fetch.
        self._ensure_lotus_objects()

        mode = self.configuration.general_params.ionization_mode
        libraries = {
            library.library_id: library
            for library in self.library_store.libraries_for_mode(mode)
        }
        if not libraries:
            self.logger.warning(
                "No spectral library registered for mode %r; MS2 added no annotations.",
                mode,
            )
            return analysis

        number_of_spectra = len(spectrum_list)
        self.logger.info(
            "Running MS2 enrichment on %d spectra against %d librar%s (%s)",
            number_of_spectra, len(libraries),
            "y" if len(libraries) == 1 else "ies",
            ", ".join(library.label for library in libraries.values()),
        )

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
                    f"Unknown spectral match method: "
                    f"{self.configuration.spectral_match_params.method!r}"
                )

        n_annotations = 0
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

            # Stage 1, in SQL: retrieve only the library spectra that could match
            # this chunk. `pairs` names which feature each candidate was found for.
            candidates, pairs = self.library_store.candidates_for(
                [spectrum.precursor_mz for spectrum in spectra_chunk],
                mode,
                self.configuration.spectral_match_params.parent_mz_tol,
            )
            if not pairs:
                continue

            # Stage 2: the actual MS/MS cosine on each surviving pair.
            for feature_idx, candidate_idx in tzip(
                [pair[0] for pair in pairs],
                [pair[1] for pair in pairs],
                desc="Processing chunk similarities",
                leave=False,
            ):
                candidate = candidates[candidate_idx]
                msms_score, n_matches = cosine_similarity.pair(
                    spectra_chunk[feature_idx], candidate.spectrum
                )[()]  # 0-dim array -> (score, n_matches)

                # min_peaks is an inclusive minimum (>=); min_score stays a strict
                # lower bound (a match must beat the floor, not merely equal it).
                if (
                    msms_score > self.configuration.spectral_match_params.min_score
                    and n_matches >= self.configuration.spectral_match_params.min_peaks
                ):
                    n_annotations += self._annotate(
                        spectra_chunk[feature_idx],
                        candidate,
                        libraries[candidate.library_id].label,
                        msms_score,
                        n_matches,
                    )

        self.logger.info("MS2 enrichment added %d annotations", n_annotations)
        # Spectra are annotated in place; the same Analysis is returned (uniform
        # enhancer contract).
        return analysis
