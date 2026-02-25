
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.models.analysis import Analysis


class WeightsEnhancer(Enhancer):
    """Enhancer that adds taxonomical and chemical weights to the annotations and reranks them."""

    def __init__(self, configuration: ReweightingConfig, logger):

        self.configuration = configuration
        self.logger = logger

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Weights Enhancer"
    
    def enrich(self, analysis: Analysis) -> Analysis:
        """Adds taxonomical and chemical weights to the annotations and reranks them."""

        best_ott_matches = analysis.ott_matches[0]
        self.logger.debug(
            f"OTT matches for the analysis: {analysis.ott_matches}"
        )
        

