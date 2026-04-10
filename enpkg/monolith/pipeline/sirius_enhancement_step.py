"""

"""

import logging

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.pipeline.base_pipeline_step import PipelineStep
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig
from enpkg.monolith.enhancers.sirius_enhancer import SiriusEnhancer

class SiriusEnhancementStep(PipelineStep):
    """
    A pipeline step that performs Sirius enhancement.
    """

    def __init__(self, config: SiriusEnhancerConfig, logger: logging.Logger):
        super().__init__(config)
        self.logger = logger

    def can_run(self, analysis: Analysis) -> bool:
        # check if analysis has spectra
        return len(analysis.spectra) > 0
    
    def process(self, analysis: Analysis) -> Analysis:
        enhancer = SiriusEnhancer(self.config, self.logger)
        try:
            enriched_analysis = enhancer.enhance(analysis)
        except Exception as e:
            raise RuntimeError(f"Error during Sirius enhancement: {e}")
        return enriched_analysis
    