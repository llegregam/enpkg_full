"""
A pipeline step that performs taxonomical enrichment from a source taxon using the Open Tree of Life API.
"""

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.pipeline.base_pipeline_step import PipelineStep
from enpkg.monolith.enhancers.taxa_enhancer import TaxaEnhancer

class TaxonomicalEnhancementStep(PipelineStep):

    def process(self, analysis: Analysis) -> Analysis:
        # 1. Guard check
        if not analysis.has_source_taxon:
            self.logger.info("Source taxon not defined, skipping.")
            return analysis
        
        # 2. Call the worker
        genus, species = analysis.genus_and_species
        enhancer = TaxaEnhancer()
        new_matches = enhancer.enhance(genus, species)

        # 3. Perform update
        return analysis.model_copy(
            update={"ott_matches": analysis.ott_matches + new_matches}
        )
    
    def can_run(self, analysis: Analysis) -> bool:
        """
        Check if the source taxon is defined for this analysis and thus if the step can run.
        """
        return analysis.has_source_taxon