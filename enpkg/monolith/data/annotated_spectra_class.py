"""Module to store annotated spectra and MSMS annotations."""

from typing import Optional, Dict, Any, Tuple
from matchms import Spectrum
import numpy as np
from scipy.stats import entropy
from enpkg.monolith.data.ms1_data_classes import ChemicalAdduct
from enpkg.monolith.data.chemical_annotation import MS2ChemicalAnnotation
# from enpkg.monolith.data.sirius_data_classes import SiriusChemicalAnnotation
from enpkg.monolith.data.lotus_class import (
    Lotus,
)
from enpkg.monolith.data.otl_class import Match


class AnnotatedSpectrum(Spectrum):
    """Class to store annotated spectra. This is an extension of the matchms Spectrum class"""

    def __init__(
        self,
        spectrum: Spectrum,
        mass_over_charge: float,
        retention_time: float,
        intensity: float,
    ):
        """Initialize the annotated spectrum."""
        super().__init__(
            mz=spectrum.mz,
            intensities=spectrum.intensities,
            metadata=spectrum.metadata,
        )

        # We verify that the provided mass over charge matches
        # the precursor mass over charge
        if abs(mass_over_charge - spectrum.get("precursor_mz")) > 0.001:
            raise ValueError(
                f"Provided mass over charge {mass_over_charge} does "
                f"not match the precursor mass over charge {spectrum.get('precursor_mz')}"
            )
        self.mass_over_charge: float = mass_over_charge
        self.retention_time: float = retention_time
        self.intensity: float = intensity
        # self._sirius_annotations: list[SiriusChemicalAnnotation] = []
        self._ms2_annotations: list[MS2ChemicalAnnotation] = []
        self._ms1_annotations: list[ChemicalAdduct] = []
        self._ms1_pathway_scores: Optional[np.ndarray] = None
        self._ms1_superclass_scores: Optional[np.ndarray] = None
        self._ms1_class_scores: Optional[np.ndarray] = None
        self._ms2_pathway_scores: Optional[np.ndarray] = None
        self._ms2_superclass_scores: Optional[np.ndarray] = None
        self._ms2_class_scores: Optional[np.ndarray] = None

    # SPECTRUM PROPERTIES
    @property
    def precursor_mz(self):
        """Return the precursor mass over charge"""
        return float(self.get("precursor_mz"))

    @property
    def polarity(self) -> bool:
        """Return the polarity of the spectrum"""
        return int(self.get("charge")) > 0

    @property
    def feature_id(self) -> int:
        """Return the feature ID of the spectrum"""
        return int(self.get("feature_id"))
    
    # NPC CLASSIFICATION SCORES (propagated)
    @property
    def ms1_pathway_scores(self) -> Optional[np.ndarray]:
        """Return the MS1 propagated NPC pathway scores"""
        return self._ms1_pathway_scores

    @property
    def ms1_superclass_scores(self) -> Optional[np.ndarray]:
        """Return the MS1 propagated NPC superclass scores"""
        return self._ms1_superclass_scores

    @property
    def ms1_class_scores(self) -> Optional[np.ndarray]:
        """Return the MS1 propagated NPC class scores"""
        return self._ms1_class_scores

    @ms1_pathway_scores.setter
    def ms1_pathway_scores(self, pathway_scores: np.ndarray):
        """Set the MS1 propagated NPC pathway scores"""
        self._ms1_pathway_scores = pathway_scores

    @ms1_superclass_scores.setter
    def ms1_superclass_scores(self, superclass_scores: np.ndarray):
        """Set the MS1 propagated NPC superclass scores"""
        self._ms1_superclass_scores = superclass_scores

    @ms1_class_scores.setter
    def ms1_class_scores(self, class_scores: np.ndarray):
        """Set the MS1 propagated NPC class scores"""
        self._ms1_class_scores = class_scores

    @property
    def ms2_pathway_scores(self) -> Optional[np.ndarray]:
        """Return the MS2 propagated NPC pathway scores"""
        return self._ms2_pathway_scores

    @property
    def ms2_superclass_scores(self) -> Optional[np.ndarray]:
        """Return the MS2 propagated NPC superclass scores"""
        return self._ms2_superclass_scores

    @property
    def ms2_class_scores(self) -> Optional[np.ndarray]:
        """Return the MS2 propagated NPC class scores"""
        return self._ms2_class_scores

    @ms2_pathway_scores.setter
    def ms2_pathway_scores(self, pathway_scores: np.ndarray):
        """Set the MS2 propagated NPC pathway scores"""
        self._ms2_pathway_scores = pathway_scores

    @ms2_superclass_scores.setter
    def ms2_superclass_scores(self, superclass_scores: np.ndarray):
        """Set the MS2 propagated NPC superclass scores"""
        self._ms2_superclass_scores = superclass_scores

    @ms2_class_scores.setter
    def ms2_class_scores(self, class_scores: np.ndarray):
        """Set the MS2 propagated NPC class scores"""
        self._ms2_class_scores = class_scores

    # ANNOTATIONS
    @property
    def ms1_annotations(self) -> list[ChemicalAdduct]:
        """Return the MS1 annotations"""
        return self._ms1_annotations
    
    @ms1_annotations.setter
    def ms1_annotations(self, annotation_list: list[ChemicalAdduct]):
        """Set the MS1 annotations"""
        self._ms1_annotations = annotation_list

    @property
    def ms2_annotations(self) -> list[MS2ChemicalAnnotation]:
        """Return the MS2 annotations"""
        return self._ms2_annotations
    
    @ms2_annotations.setter
    def ms2_annotations(self, annotation_list: list[MS2ChemicalAnnotation]):
        """Set the MS2 annotations"""
        self._ms2_annotations = annotation_list

    def has_ms1_annotations(self) -> bool:
        """Returns whether the spectrum has MS1 annotations"""
        return len(self._ms1_annotations) > 0

    def has_ms2_annotations(self) -> bool:
        """Returns whether the spectrum has MS2 annotations"""
        return len(self._ms2_annotations) > 0

    def add_ms2_annotation(self, annotation: MS2ChemicalAnnotation):
        """Add an MS2 annotation to the spectrum."""
        self._ms2_annotations.append(annotation)

    # def add_sirius_annotation(self, annotation: SiriusChemicalAnnotation):
    #     """Add a Sirius annotation to the spectrum."""
    #     self._sirius_annotations.append(annotation)

    def get_top_k_lotus_annotation(self, k: int = 1) -> Optional[list[Lotus]]:
        """Returns the top k best LOTUS annotations from the MS1 adduct annotations.


        Parameters
        ----------
        k : int
            The number of top LOTUS annotations to return.

        Returns
        -------
        Optional[list[Lotus]]
            The top k best LOTUS annotations.
            If the spectrum has no annotations, returns None.
        """
        if not self.has_ms2_annotations() and not self.has_ms1_annotations():
            return None

        annotations: Dict[Lotus, float] = {}

        # NOTE: under the slimmed MS2 model, MS2 annotations no longer carry
        # Lotus objects (only short_inchikey + organisms), so they cannot
        # contribute Lotus entries to this helper. Only MS1 adducts (which still
        # hold Lotus) feed it. This method is currently unused; revisit if a
        # Lotus-free MS2 top-k is needed.
        for annotation in self._ms1_annotations:
            for lotus in annotation.lotus:
                pathway_score = np.mean(
                    lotus.structure_taxonomy_hammer_pathways
                    * self._ms1_pathway_scores
                )
                superclass_score = np.mean(
                    lotus.structure_taxonomy_hammer_superclasses
                    * self._ms1_superclass_scores
                )
                class_score = np.mean(
                    lotus.structure_taxonomy_hammer_classes
                    * self._ms1_class_scores
                )

                combined_score: float = pathway_score * superclass_score * class_score
                annotations[lotus] = annotations.get(lotus, 0) + combined_score

        return sorted(annotations, key=annotations.get, reverse=True)[:k]

    def get_top_k_ms2_structures(self, k: int = 1) -> Optional[list[str]]:
        """Returns the top-k matched structures (short InChIKeys) from the MS2
        annotations, ranked by how well each structure's NPC classification
        aligns with this spectrum's MS2-propagated class scores.

        MS2 analogue of ``get_top_k_lotus_annotation``. The slimmed MS2
        annotation no longer carries Lotus objects, so structures are identified
        by short InChIKey rather than returned as Lotus instances.

        Requires the MS2 propagated scores (set by the WeightsEnhancer). Returns
        None if there are no MS2 annotations or those scores are not yet set.

        Parameters
        ----------
        k : int
            The number of top structures to return.
        """
        if not self.has_ms2_annotations():
            return None
        if (
            self._ms2_pathway_scores is None
            or self._ms2_superclass_scores is None
            or self._ms2_class_scores is None
        ):
            return None

        scores: Dict[str, float] = {}
        for annotation in self._ms2_annotations:
            pathway_score = np.mean(
                annotation.get_pathway_scores() * self._ms2_pathway_scores
            )
            superclass_score = np.mean(
                annotation.get_superclass_scores() * self._ms2_superclass_scores
            )
            class_score = np.mean(
                annotation.get_class_scores() * self._ms2_class_scores
            )

            combined_score: float = pathway_score * superclass_score * class_score
            scores[annotation.short_inchikey] = (
                scores.get(annotation.short_inchikey, 0) + combined_score
            )

        return sorted(scores, key=scores.get, reverse=True)[:k]