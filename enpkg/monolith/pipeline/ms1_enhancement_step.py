import logging

from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.pipeline.base_pipeline_step import Analysis, PipelineStep
from enpkg.monolith.loaders.lotus_store import LotusStore


class MS1EnhancementStep(PipelineStep):
    """
    A pipeline step that performs MS1 enhancement.
    """

    def __init__(
        self,
        config: MSEnhancerConfig,
        logger: logging.Logger,
        lotus_store: LotusStore,
    ):

        super().__init__(config)
        self.logger = logger
        self.lotus_store = lotus_store

    def can_run(self, analysis: Analysis) -> bool:
        # check if analysis has spectra

        return len(analysis.spectra) > 0

    def process(self, analysis: Analysis) -> Analysis:

        enhancer = MS1Enhancer(self.config, self.logger, self.lotus_store)
        try:
            enriched_spectra = enhancer.enhance(analysis.spectra)
        except Exception as e:
            self.logger.error(f"Error during MS1 enhancement: {e}")
            raise
        self.logger.info("MS1 enrichment completed.")
        return analysis.model_copy(update={"spectra": enriched_spectra})