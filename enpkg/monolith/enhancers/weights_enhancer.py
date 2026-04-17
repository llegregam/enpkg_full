
import logging
from typing import Optional

from tqdm import tqdm
import numpy as np

from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.utils.label_propagation_algorithm import label_propagation_algorithm


class WeightsEnhancer(Enhancer):
    """Enhancer that adds taxonomical and chemical weights to the annotations and reranks them."""

    def __init__(self, configuration:ReweightingConfig, logger: logging.Logger, db_loader: DBLoader):

        self.configuration = configuration
        self.logger = logger
        self.db_loader = db_loader 
        self.logger.info("Loading Databases")
        self.db_loader.load_taxonomical_databases()


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
                taxonomical_similarities, spectrum.ms1_annotations
            ):
                pathway_features[i] += (
                    taxonomical_similarity * adduct.get_hammer_pathway_scores()
                )

                superclass_features[i] += (
                    taxonomical_similarity * adduct.get_hammer_superclass_scores()
                )

                class_features[i] += (
                    taxonomical_similarity * adduct.get_hammer_class_scores()
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
                    annotation.scores["cosine_similarity"].get("value")
                    for annotation in spectrum.ms2_annotations
                    if annotation.has_lotus_entries()
                ),
                dtype=np.float32,
            )

            if best_ott_match is not None:
                taxonomical_similarities: np.ndarray = np.fromiter(
                    (
                        annotation.maximal_normalized_taxonomical_similarity(best_ott_match)
                        for annotation in spectrum.ms2_annotations
                        if annotation.has_lotus_entries()
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
                f"Spectrum {i}: chemical similarities = {chemical_similarities},\n" /
                f"Taxonomical similarities = {taxonomical_similarities},\n" /
                f"Combined similarities = {combined_similarities},\n" /
                f"Total combined similarities = {total_combined_similarities}"
            )

            if total_combined_similarities > 0:
                # We normalize the combined similarity scores
                combined_similarities /= total_combined_similarities

            for ms2_annotation, combined_similarity in zip(
                (
                    annotation for annotation in spectrum.ms2_annotations
                    if annotation.has_lotus_entries()
                ),
                combined_similarities,
            ):
                pathway_features[i] += (
                    combined_similarity * ms2_annotation.get_hammer_pathway_scores()
                )

                superclass_features[i] += (
                    combined_similarity * ms2_annotation.get_hammer_superclass_scores()
                )

                class_features[i] += (
                    combined_similarity * ms2_annotation.get_hammer_class_scores()
                )
    
    def enhance(self, analysis: Analysis) -> Analysis:
        """Adds taxonomical and chemical weights to the annotations and reranks them."""

        self._number_of_pathways = self.db_loader._number_of_pathways
        self._number_of_superclasses = self.db_loader._number_of_superclasses
        self._number_of_classes = self.db_loader._number_of_classes

        pathway_features, superclass_features, class_features = self.compute_ms1_classifications(analysis)

        loading_bar = tqdm(
            desc="Computing LPA scores",
            dynamic_ncols=True,
            leave=False,
            total=3,
        )

        propagated_pathway = label_propagation_algorithm(
            graph=analysis.molecular_network,
            node_names=analysis.feature_ids,
            features=pathway_features,
            normalize=False,
        )

        loading_bar.update(1)

        propagated_superclass = label_propagation_algorithm(
            graph=analysis.molecular_network,
            node_names=analysis.feature_ids,
            features=superclass_features,
            normalize=False,
        )

        loading_bar.update(1)

        propagated_class = label_propagation_algorithm(
            graph=analysis.molecular_network,
            node_names=analysis.feature_ids,
            features=class_features,
            normalize=False,
        )

        loading_bar.update(1)
        loading_bar.close()

        for i, spectrum in enumerate(analysis.spectra):
            spectrum.ms1_pathway_scores = propagated_pathway[i]
            spectrum.ms1_superclass_scores = propagated_superclass[i]
            spectrum.ms1_class_scores = propagated_class[i]

        # TODO: Think about modifying analysis in place vs returning a new one. 
        return analysis