"""MS1 graph enhancer: relate features that are adducts of the same molecule.

Builds the mzAdan-style adduct-relationship graph over the analysis' feature
precursor m/z's, resolves each cluster's base ion (``[M+H]+`` / ``[M-H]-``), and
stamps the resolution (cluster id, role, assigned ionization form, and the
CGC/CIC/CCC confidence indices) onto each spectrum. The graph itself is attached
to the returned ``Analysis``. Downstream, the MS1 enhancer reads these roles to
decide how to fetch LOTUS candidates. See ``docs/MS1_GRAPH_ENHANCER.md`` and the
pure algorithm in ``enpkg/monolith/utils/ms1_adduct_graph.py``.
"""

from logging import Logger

from enpkg.monolith.configuration.ms1_graph_enhancer_config import MS1GraphEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.enhancers.graph_adducts import graph_recipes_for_polarity
from enpkg.monolith.utils.ms1_adduct_graph import GraphPeak, resolve_adduct_graph


class MS1GraphEnhancer(Enhancer):
    """Enhancer that attaches the MS1 adduct-relationship graph to the analysis."""

    def __init__(self, configuration: MS1GraphEnhancerConfig, logger: Logger):
        if not isinstance(configuration, MS1GraphEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type MS1GraphEnhancerConfig, got {type(configuration)}"
            )
        self.configuration = configuration
        self.logger = logger

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS1 Graph Enhancer"

    def enhance(self, analysis: Analysis) -> Analysis:
        """Resolve adduct clusters and attach the graph to the analysis.

        Spectra are stamped in place with their cluster resolution; the graph is
        attached via ``model_copy`` (Analysis is immutable), matching the network
        enhancer's pattern.
        """
        polarity = self.configuration.general_params.polarity
        recipes, base_recipe = graph_recipes_for_polarity(polarity)

        # Node ids are the raw feature ids (as the molecular network uses), so the
        # graph lines up with analysis.feature_ids for the integrity validator.
        peaks = [
            GraphPeak(
                feature_id=feature_id,
                mz=spectrum.precursor_mz,
                retention_time=spectrum.retention_time,
                intensity=spectrum.intensity,
            )
            for feature_id, spectrum in zip(
                analysis.feature_ids, analysis.spectra, strict=True
            )
        ]

        graph, results = resolve_adduct_graph(
            peaks,
            recipes,
            base_recipe,
            tol=self.configuration.mz_tolerance,
            rt_tolerance=self.configuration.rt_tolerance_min,
        )

        for feature_id, spectrum in zip(
            analysis.feature_ids, analysis.spectra, strict=True
        ):
            result = results[feature_id]
            spectrum.ms1_cluster_id = result.cluster_id
            spectrum.ms1_cluster_role = result.role
            spectrum.ms1_assigned_recipe = result.assigned_recipe
            spectrum.ms1_cluster_connectivity = result.cluster_connectivity
            spectrum.ms1_cluster_intensity_coverage = result.cluster_intensity_coverage
            spectrum.ms1_cluster_count_coverage = result.cluster_count_coverage

        n_clusters = len({r.cluster_id for r in results.values() if r.cluster_id is not None})
        n_singletons = sum(1 for r in results.values() if r.role == "singleton")
        self.logger.info(
            "MS1 adduct graph: %d features -> %d clusters, %d singletons",
            len(peaks),
            n_clusters,
            n_singletons,
        )

        return analysis.model_copy(update={"ms1_adduct_graph": graph})
