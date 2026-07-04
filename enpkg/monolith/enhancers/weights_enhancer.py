
import logging
from typing import Optional

import numpy as np
from tqdm import tqdm

from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.utils.label_propagation_algorithm import label_propagation_algorithm


class WeightsEnhancer(Enhancer):
    """Enhancer that adds taxonomical and chemical weights to the annotations and reranks them."""

    def __init__(self, configuration: ReweightingConfig, logger: logging.Logger, lotus_store: LotusStore):

        self.configuration = configuration
        self.logger = logger
        # The weights enhancer only needs the pathway / superclass / class counts,
        # which LotusStore resolves up-front from the DuckDB metadata table.
        self.lotus_store = lotus_store


    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Weights Enhancer"

    def compute_ms1_classifications(self, analysis: Analysis) -> tuple[np.ndarray, np.ndarray, np.ndarray]:

        pathway_features = np.zeros(
            (analysis.number_of_spectra, self._number_of_pathways), dtype=np.float32
        )
        superclass_features = np.zeros(
            (analysis.number_of_spectra, self._number_of_superclasses),
            dtype=np.float32,
        )
        class_features = np.zeros(
            (analysis.number_of_spectra, self._number_of_classes), dtype=np.float32
        )
        best_ott_match: Optional[Match] = analysis.best_ott_matches

        for i, spectrum in tqdm(
            enumerate(analysis.spectra),
            leave=False,
            total=analysis.number_of_spectra,
            desc="Computing MS1 NPC scores",
            dynamic_ncols=True,
        ):
            # If the spectrum has no adducts, we cannot make assumptions regarding its scores,
            # and therefore we give uniform scores to all pathways, superclasses, and classes.
            if not spectrum.has_ms1_annotations():
                pathway_features[i] = np.zeros(
                    shape=(self._number_of_pathways,),
                )
                superclass_features[i] = np.zeros(
                    shape=(self._number_of_superclasses,),
                )
                class_features[i] = np.zeros(
                    shape=(self._number_of_classes,),
                )
                continue

            # Now that we have determined the adducts potentially associated with this
            # spectrum, we can populate the associated features with the adducts' pathway,
            # superclass, and class annotations, weighted by the adduct's normalized
            # taxonomical similarity score.

            # First, we compute the maximal normalized taxonomical similarity score for
            # each adducts, if we do have a known sample taxonomy match.

            if best_ott_match is not None:
                taxonomical_similarities: np.ndarray = np.fromiter(
                    (
                        adduct.maximal_normalized_taxonomical_similarity(best_ott_match)
                        for adduct in spectrum.ms1_annotations
                    ),
                    dtype=np.float32,
                )
            else:
                taxonomical_similarities: np.ndarray = np.ones(
                    shape=(len(spectrum.ms1_annotations),), dtype=np.float32
                )

            total_taxonomical_similarities = np.sum(taxonomical_similarities)
            if total_taxonomical_similarities > 0:
                taxonomical_similarities /= total_taxonomical_similarities

            for taxonomical_similarity, adduct in zip(
                taxonomical_similarities, spectrum.ms1_annotations, strict=False
            ):
                pathway_features[i] += (
                    taxonomical_similarity * adduct.get_pathway_scores()
                )

                superclass_features[i] += (
                    taxonomical_similarity * adduct.get_superclass_scores()
                )

                class_features[i] += (
                    taxonomical_similarity * adduct.get_class_scores()
                )
        return pathway_features, superclass_features, class_features

    def compute_ms2_classifications(self, analysis: Analysis) -> tuple[np.ndarray, np.ndarray, np.ndarray]:

        pathway_features = np.zeros(
            (analysis.number_of_spectra, self._number_of_pathways), dtype=np.float32
        )
        superclass_features = np.zeros(
            (analysis.number_of_spectra, self._number_of_superclasses),
            dtype=np.float32,
        )
        class_features = np.zeros(
            (analysis.number_of_spectra, self._number_of_classes), dtype=np.float32
        )
        best_ott_match: Optional[Match] = analysis.best_ott_matches

        for i, spectrum in tqdm(
            enumerate(analysis.spectra),
            leave=False,
            total=analysis.number_of_spectra,
            desc="Computing MS2 NPC scores",
            dynamic_ncols=True,
        ):

            if not spectrum.has_ms2_annotations():
                continue

            chemical_similarities: np.ndarray = np.fromiter(
                (
                    annotation.score
                    for annotation in spectrum.ms2_annotations
                    if annotation.has_organisms()
                ),
                dtype=np.float32,
            )

            if best_ott_match is not None:
                taxonomical_similarities: np.ndarray = np.fromiter(
                    (
                        annotation.maximal_normalized_taxonomical_similarity(best_ott_match)
                        for annotation in spectrum.ms2_annotations
                        if annotation.has_organisms()
                    ),
                    dtype=np.float32,
                )
            else:
                taxonomical_similarities: np.ndarray = np.ones(
                    chemical_similarities.size, dtype=np.float32
                )

            combined_similarities: np.ndarray = (chemical_similarities * taxonomical_similarities)
            total_combined_similarities = np.sum(combined_similarities)
            self.logger.debug(
                f"Spectrum {i}: chemical similarities = {chemical_similarities},\n"
                f"Taxonomical similarities = {taxonomical_similarities},\n"
                f"Combined similarities = {combined_similarities},\n"
                f"Total combined similarities = {total_combined_similarities}"
            )

            if total_combined_similarities > 0:
                # We normalize the combined similarity scores
                combined_similarities /= total_combined_similarities

            for ms2_annotation, combined_similarity in zip(
                (
                    annotation for annotation in spectrum.ms2_annotations
                    if annotation.has_organisms()
                ),
                combined_similarities,
                strict=False,
            ):
                pathway_features[i] += (
                    combined_similarity * ms2_annotation.get_pathway_scores()
                )

                superclass_features[i] += (
                    combined_similarity * ms2_annotation.get_superclass_scores()
                )

                class_features[i] += (
                    combined_similarity * ms2_annotation.get_class_scores()
                )

        return pathway_features, superclass_features, class_features

    def _propagate_over_network(
        self,
        analysis: Analysis,
        features: tuple[np.ndarray, np.ndarray, np.ndarray],
        desc: str,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run label propagation over the molecular network for the 3 NPC levels.

        Returns the propagated (pathway, superclass, class) score matrices in the
        same order as ``features``.
        """
        propagated = []
        bar = tqdm(desc=desc, dynamic_ncols=True, leave=False, total=len(features))
        for feature_matrix in features:
            propagated.append(
                label_propagation_algorithm(
                    graph=analysis.molecular_network,
                    node_names=analysis.feature_ids,
                    features=feature_matrix,
                    normalize=False,
                )
            )
            bar.update(1)
        bar.close()
        return tuple(propagated)

    @staticmethod
    def _assign_scores(
        analysis: Analysis,
        prefix: str,
        propagated: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> None:
        """Write propagated (pathway, superclass, class) rows onto each spectrum."""
        pathway, superclass, klass = propagated
        for i, spectrum in enumerate(analysis.spectra):
            setattr(spectrum, f"{prefix}_pathway_scores", pathway[i])
            setattr(spectrum, f"{prefix}_superclass_scores", superclass[i])
            setattr(spectrum, f"{prefix}_class_scores", klass[i])

    def enhance(self, analysis: Analysis) -> Analysis:
        """Reweight MS1 and MS2 annotations by propagating NPC scores over the network.

        For each level (MS1 adducts, MS2 ISDB matches) the per-spectrum NPC feature
        matrices are computed, propagated over the molecular network via label
        propagation, and written back onto the spectra. Spectra are updated in
        place and the same Analysis is returned (uniform enhancer contract).
        """
        # Classification counts now come from the LotusStore (DuckDB-resolved at
        # construction time), not from DBLoader DataFrames.
        self._number_of_pathways = self.lotus_store.number_of_pathways
        self._number_of_superclasses = self.lotus_store.number_of_superclasses
        self._number_of_classes = self.lotus_store.number_of_classes

        for prefix, features in (
            ("ms1", self.compute_ms1_classifications(analysis)),
            ("ms2", self.compute_ms2_classifications(analysis)),
        ):
            propagated = self._propagate_over_network(
                analysis, features, desc=f"Computing {prefix.upper()} LPA scores"
            )
            self._assign_scores(analysis, prefix, propagated)

        return analysis
