"""Submodule for the MS2 spectral-matching enhancer."""

import re
from collections import Counter
from itertools import groupby
from logging import Logger
from time import time
from typing import Optional

import numpy as np
from matchms.similarity import CosineGreedy, CosineHungarian
from tqdm.auto import trange
from tqdm.contrib import tzip

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.data.chemical_annotation import (
    AnnotationOrganism,
    LibraryStructure,
    MS2ChemicalAnnotation,
)
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.loaders.spectral_library_store import (
    LibraryCandidate,
    SpectralLibraryStore,
)
from enpkg.monolith.utils.ms2_adduct_gating import select_spectra_for_ms2

# Standard InChIKey: 14-letter skeleton block, 10-letter stereo/version block,
# 1-letter protonation flag. Library exports can carry other strings in their
# InChIKey field (a SMILES such as "CCCCCCCCCCCCCC" is 14 uppercase letters too),
# so the full key's shape is what tells a real one apart.
_INCHIKEY = re.compile(r"[A-Z]{14}-[A-Z]{10}-[A-Z]")


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
    ) -> str:
        """Attach one MS2 annotation for a scored candidate.

        Returns which path was taken:

        * ``"lotus"`` — LOTUS knows the structure, so the annotation carries its
          classification vectors and every source organism LOTUS lists for it.
        * ``"library"`` — LOTUS does not know it, so the annotation carries what the
          library asserts about the structure, zero-filled classification vectors
          and no organisms. Having no organism keeps it out of the reweighting
          (``WeightsEnhancer`` filters on ``has_organisms()``); the zero vectors are
          full length so every elementwise product downstream still lines up.
        * ``"no_hit"`` — the library row names no structure, so there is nothing to
          annotate and no annotation is added.
        * ``"malformed_inchikey"`` — LOTUS does not know it and its InChIKey is not
          a well-formed InChIKey. The full key would become the structure node's
          identity and the 2D key is derived from it, so neither can be trusted and
          no annotation is added. ``enhance`` reports the offending strings.
        """
        if not candidate.short_inchikey:
            return "no_hit"

        lotus_entries = self._lotus_by_short_inchikey.get(candidate.short_inchikey)
        if lotus_entries:
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
                    lotus=representative,
                )
            )
            return "lotus"

        inchikey = candidate.inchikey
        if not inchikey:
            return "no_hit"
        if not _INCHIKEY.fullmatch(inchikey):
            return "malformed_inchikey"
        spectrum.add_ms2_annotation(
            MS2ChemicalAnnotation(
                # `source` names where the structure detail came from; the serializer
                # emits it as prov:wasDerivedFrom.
                source=library_label,
                queried_against=library_label,
                short_inchikey=candidate.short_inchikey,
                score=float(score),
                n_matched_peaks=int(n_matches),
                pathway_scores=np.zeros(self.lotus_store.number_of_pathways, dtype=np.float32),
                superclass_scores=np.zeros(
                    self.lotus_store.number_of_superclasses, dtype=np.float32
                ),
                class_scores=np.zeros(self.lotus_store.number_of_classes, dtype=np.float32),
                organisms=[],
                library_structure=self._library_structure(candidate, inchikey),
            )
        )
        return "library"

    @staticmethod
    def _library_structure(candidate: LibraryCandidate, inchikey: str) -> LibraryStructure:
        """The structure metadata a candidate asserts, filed under ``inchikey``.

        ``inchikey`` is the candidate's own key, already checked to be well formed.
        """
        return LibraryStructure(
            inchikey=inchikey,
            inchi=candidate.inchi,
            smiles=candidate.smiles,
            molecular_formula=candidate.molecular_formula,
            compound_name=candidate.compound_name,
            npc_pathway=candidate.npc_pathway,
            npc_superclass=candidate.npc_superclass,
            npc_class=candidate.npc_class,
            classyfire_superclass=candidate.classyfire_superclass,
            classyfire_class=candidate.classyfire_class,
            classyfire_subclass=candidate.classyfire_subclass,
        )

    @staticmethod
    def _keep_best_match_per_structure(
        spectrum: AnnotatedSpectrum,
    ) -> list[MS2ChemicalAnnotation]:
        """Reduce the spectrum's MS2 annotations to one per library and structure.

        A feature can match one structure through several library spectra: several
        spectra of one compound, or one spectrum that two source collections both
        supplied. All of them would describe the same graph node, which is keyed on
        library and 2D InChIKey, and the reweighting would count the structure once per
        spectrum. Only the best is kept: highest score, then most matched peaks, then
        the earliest in the list. Each structure stays where it first appeared.

        Returns:
            The annotations dropped.
        """
        best: dict[tuple[Optional[str], str], MS2ChemicalAnnotation] = {}
        for annotation in spectrum.ms2_annotations:
            key = (annotation.queried_against, annotation.short_inchikey)
            kept = best.get(key)
            if kept is None or (annotation.score, annotation.n_matched_peaks) > (
                kept.score, kept.n_matched_peaks
            ):
                best[key] = annotation
        if len(best) == len(spectrum.ms2_annotations):
            return []
        kept_ids = {id(annotation) for annotation in best.values()}
        dropped = [a for a in spectrum.ms2_annotations if id(a) not in kept_ids]
        spectrum.ms2_annotations = list(best.values())
        return dropped

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
        ``min_peaks`` are appended in place to ``spectrum.ms2_annotations``, whether
        or not LOTUS knows the matched structure — see :meth:`_annotate` for what
        each kind carries. Each feature then keeps one annotation per library and
        structure, from its best-scoring library spectrum
        (:meth:`_keep_best_match_per_structure`).
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

        outcomes: Counter[str] = Counter()
        merged: Counter[str] = Counter()
        malformed_inchikeys: set[str] = set()
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
                    outcome = self._annotate(
                        spectra_chunk[feature_idx],
                        candidate,
                        libraries[candidate.library_id].label,
                        msms_score,
                        n_matches,
                    )
                    outcomes[outcome] += 1
                    if outcome == "malformed_inchikey":
                        malformed_inchikeys.add(candidate.inchikey)

            for spectrum in spectra_chunk:
                for annotation in self._keep_best_match_per_structure(spectrum):
                    merged["lotus" if annotation.source == "Lotus" else "library"] += 1

        lotus_backed = outcomes["lotus"] - merged["lotus"]
        library_only = outcomes["library"] - merged["library"]
        self.logger.info(
            "MS2 enrichment added %d annotations (%d LOTUS-backed, %d library-only)",
            lotus_backed + library_only, lotus_backed, library_only,
        )
        if merged:
            self.logger.info(
                "%d further scored matches named a structure already matched on the same "
                "feature; each structure keeps its best-scoring match",
                merged.total(),
            )
        if outcomes["no_hit"]:
            self.logger.warning(
                "%d scored matches named no structure and were skipped", outcomes["no_hit"]
            )
        if malformed_inchikeys:
            self.logger.warning(
                "%d scored matches carried a malformed InChIKey and were skipped. "
                "%d distinct value%s found in the library's InChIKey field: %s",
                outcomes["malformed_inchikey"],
                len(malformed_inchikeys),
                "" if len(malformed_inchikeys) == 1 else "s",
                ", ".join(repr(key) for key in sorted(malformed_inchikeys)),
            )
        # Spectra are annotated in place; the same Analysis is returned (uniform
        # enhancer contract).
        return analysis
