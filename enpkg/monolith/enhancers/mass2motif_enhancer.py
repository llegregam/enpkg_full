"""Enhancer attaching MS2LDA Mass2Motif memberships from a fitted model folder."""

from logging import Logger

import polars as pl

from enpkg.monolith.configuration.mass2motif_config import Mass2MotifConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.mass2motif import MotifMembership
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.loaders.mass2motif_model import Mass2MotifModel

# Largest difference (Da) between the precursor m/z the fit read and the one the
# analysis holds before a feature id is taken to name a different spectrum. The same
# tolerance AnnotatedSpectrum applies between the quant table and the spectra file.
PRECURSOR_TOLERANCE = 0.001


class Mass2MotifEnhancer(Enhancer):
    """Attach the training-run motif memberships of a model folder to the analysis.

    The memberships were computed when the model was fitted (``enpkg motifs fit``),
    for every spectrum of every experiment in the fit; this looks up the analysis'
    run name and feature ids. The analysis is returned unchanged, with a warning, when
    the model was fitted in the other ionization mode or did not include the run.
    """

    def __init__(self, config: Mass2MotifConfig, logger: Logger):
        if not isinstance(config, Mass2MotifConfig):
            raise TypeError(f"Expected Mass2MotifConfig, got {type(config).__name__}")
        self.config = config
        self.logger = logger

    def name(self) -> str:
        return "mass2motif"

    def enhance(self, analysis: Analysis) -> Analysis:
        """Set ``motif_memberships`` on each spectrum and ``motif_set`` on the analysis.

        Raises:
            ValueError: the model's training documents for this run repeat a feature
                id, or name a feature whose precursor m/z differs from the analysis'
                (the spectra file changed after the fit).
        """
        model = Mass2MotifModel.load(self.config.model_dir)
        mode = self.config.general_params.ionization_mode
        if model.motif_set.ionization_mode != mode:
            self.logger.warning(
                "Mass2Motif model %s was fitted on %s-mode spectra; this run is %s-mode. "
                "No motifs attached.", model.motif_set.model_id, model.motif_set.ionization_mode, mode,
            )
            return analysis
        if analysis.run_name not in model.run_names:
            self.logger.warning(
                "Run %s is not among the experiments Mass2Motif model %s was fitted on. "
                "No motifs attached; refit with this experiment included.",
                analysis.run_name, model.motif_set.model_id,
            )
            return analysis

        self._check_documents(analysis, model.documents(analysis.run_name))
        memberships = (
            model.memberships(analysis.run_name)
            .filter(
                (pl.col("probability") >= self.config.min_probability)
                & (pl.col("overlap") >= self.config.min_overlap)
            )
            .sort(["feature_id", "probability"], descending=[False, True])
        )
        by_feature: dict[int, list[MotifMembership]] = {}
        for feature_id, motif, probability, overlap in memberships.iter_rows():
            by_feature.setdefault(feature_id, []).append(
                MotifMembership(motif_index=motif, probability=probability, overlap=overlap)
            )
        for spectrum in analysis.spectra:
            spectrum.motif_memberships = by_feature.get(spectrum.feature_id, [])
        return analysis.model_copy(update={"motif_set": model.motif_set})

    def _check_documents(self, analysis: Analysis, documents: pl.DataFrame) -> None:
        """Raise unless each training document of the run names a distinct feature
        whose precursor m/z matches the analysis'."""
        duplicated = documents.filter(pl.col("feature_id").is_duplicated())["feature_id"].unique()
        if duplicated.len():
            raise ValueError(
                f"The Mass2Motif model has several spectra for feature id(s) "
                f"{sorted(duplicated.to_list())[:5]} of run {analysis.run_name}."
            )
        precursors = {s.feature_id: s.precursor_mz for s in analysis.spectra}
        for feature_id, precursor_mz in documents.select("feature_id", "precursor_mz").iter_rows():
            expected = precursors.get(feature_id)
            if expected is None or precursor_mz is None:
                continue
            if abs(expected - precursor_mz) > PRECURSOR_TOLERANCE:
                raise ValueError(
                    f"Feature {feature_id} of run {analysis.run_name} has precursor m/z "
                    f"{expected:.4f} here but {precursor_mz:.4f} in the Mass2Motif model's "
                    "training data: the spectra file changed after the fit. Refit the model."
                )
