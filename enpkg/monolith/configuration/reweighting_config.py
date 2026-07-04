from typing import Self

from pydantic import BaseModel, Field, model_validator

from enpkg.monolith.configuration.config import EnhancerConfig
from enpkg.monolith.configuration.MSEnhancer_config import DownloaderParams


class ReweightingParams(BaseModel):
    """Parameters for result reweighting and scoring.

    RESERVED — not yet consumed. The WeightsEnhancer currently produces the
    propagated NPC score vectors (taxonomy weight × label propagation, both
    hard-coded), and the RDF serializer ranks candidates by NPC alignment. The
    configurable blend below (msms/taxo/chemo weights, min-score thresholds,
    top-N) is the planned final-ranking feature and has no effect until that
    ranking is wired (see docs/REFACTORING_PLAN.md B-08). Tuning these values
    will not change results yet.
    """

    top_to_output: int = Field(
        default=5,
        ge=1,
        description="Number of top candidates to output"
    )
    use_post_taxo: bool = Field(
        default=True,
        description="Whether to use post-processing taxonomic filtering"
    )
    top_N_chemical_consistency: int = Field(
        default=5,
        ge=1,
        description="Number of top candidates for chemical consistency scoring"
    )
    min_score_taxo_ms1: float = Field(
        default=0,
        ge=0,
        description="Minimum taxonomic score for MS1 matches"
    )
    min_score_chemo_ms1: float = Field(
        default=0,
        ge=0,
        description="Minimum chemical score for MS1 matches"
    )
    msms_weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Weight for MS/MS spectral similarity in final score"
    )
    taxo_weight: float = Field(
        default=0.5,
        ge=0.0,
        description="Weight for taxonomic scoring in final score"
    )
    chemo_weight: float = Field(
        default=0.5,
        ge=0.0,
        description="Weight for chemical consistency in final score"
    )

    @model_validator(mode='after')
    def validate_weights_sum(self) -> Self:
        """Warn if weights don't sum to a reasonable value."""
        total = self.msms_weight + self.taxo_weight + self.chemo_weight
        if total == 0:
            raise ValueError("At least one weight must be greater than 0")
        return self


class ReweightingConfig(EnhancerConfig):
    """Configuration for the Weights Enhancer."""

    reweighting_params: ReweightingParams = Field(
        default_factory=ReweightingParams,
        description="Parameters for score reweighting"
    )

    downloader_params: DownloaderParams = Field(
        default_factory=DownloaderParams,
        description="Parameters for controlling database downloading behavior"
    )
