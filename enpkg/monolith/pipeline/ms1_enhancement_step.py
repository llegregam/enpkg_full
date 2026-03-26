import logging

from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.pipeline.base_pipeline_step import Analysis, PipelineStep
from enpkg.monolith.loaders.database_loader import DBLoader


class MS1EnhancementStep(PipelineStep):
    """
    A pipeline step that performs MS1 enhancement.
    """

    def __init__(self, config: MSEnhancerConfig, logger: logging.Logger, db_loader: DBLoader):

        super().__init__(config)
        self.logger = logger
        self.db_loader = db_loader

    def can_run(self, analysis: Analysis) -> bool:
        # check if analysis has spectra
        
        return len(analysis.spectra) > 0
    
    def process(self, analysis: Analysis) -> Analysis:
        
        enhancer = MS1Enhancer(self.config, self.logger, self.db_loader)
        try:
            enriched_spectra = enhancer.enhance(analysis.spectra)
        except Exception as e:
            self.logger.error(f"Error during MS1 enhancement: {e}")
            raise
        self.logger.info("MS1 enrichment completed.")
        return analysis.model_copy(update={"spectra": enriched_spectra})