import logging

from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.pipeline.base_pipeline_step import Analysis, PipelineStep


class MS1EnhancementStep(PipelineStep):
    """
    A pipeline step that performs MS1 enhancement.
    """

    def __init__(self, config: MS1EnhancerConfig, logger: logging.Logger):

        super().__init__(config)
        self.logger = logger

    def can_run(self, analysis: Analysis) -> bool:
        # check if analysis has spectra
        return len(analysis.spectra) > 0
    
    def process(self, analysis: Analysis) -> Analysis:
        
        enhancer = MS1Enhancer(self.config, self.logger)
        try:
            enriched_spectra = enhancer.enhance(analysis.spectra)
        except Exception as e:
            self.logger.error(f"Error during MS1 enhancement: {e}")
            raise
        self.logger.info("MS1 enrichment completed.")
        return analysis.model_copy(update={"spectra": enriched_spectra})