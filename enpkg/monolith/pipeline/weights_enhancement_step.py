import logging

from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.pipeline.base_pipeline_step import PipelineStep
from enpkg.monolith.enhancers.weights_enhancer import WeightsEnhancer
from enpkg.monolith.loaders.database_loader import DBLoader


class WeightsEnhancementStep(PipelineStep):
    """
    A pipeline step that computes taxonomical and chemical weights
    and reranks annotations using label propagation.
    """

    def __init__(self, config: ReweightingConfig, logger: logging.Logger, db_loader: DBLoader):
        super().__init__(config)
        self.logger = logger
        self.db_loader = db_loader

    def can_run(self, analysis: Analysis) -> bool:
        return len(analysis.spectra) > 0 and analysis.molecular_network is not None

    def process(self, analysis: Analysis) -> Analysis:
        enhancer = WeightsEnhancer(self.config, self.logger, self.db_loader)
        try:
            analysis = enhancer.enhance(analysis)
        except Exception as e:
            self.logger.error(f"Error during weights enhancement: {e}")
            raise
        self.logger.info("Weights enhancement completed.")
        return analysis
