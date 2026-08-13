from typing import Self

from pydantic import Field, model_validator

from enpkg.monolith.configuration.config import EnhancerConfig


class NetworkEnhancerConfig(EnhancerConfig):
    """
    Configuration for the NetworkEnhancer .
    """

    mn_msms_mz_tol: float = Field(
        0.01,
        description="The parent mass tolerance to use for spectral matching (in Da) (if cosine)"
        )
    mn_score_cutoff: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="The minimal modified cosine score for edge creation"
        )
    mn_top_n: int = Field(
        15,
        gt=0,
        description="Candidate pool per node: an edge is only considered if the partner ranks "
        "among this node's N most similar spectra. With the 'mutual' link method the pairing "
        "must be reciprocal — each spectrum must be in the other's top-N. Must be greater "
        "than mn_max_links."
        )
    mn_max_links: int = Field(
        10,
        gt=0,
        description="Degree cap: the maximum number of edges kept per node, applied after the "
        "score cutoff and the top-N candidate filter. Must be smaller than mn_top_n."
        )

    @model_validator(mode='after')
    def check_n_gt_max_links(self) -> Self:
        """Ensure mn_top_n is always greater than mn_max_links."""
        if self.mn_top_n <= self.mn_max_links:
            raise ValueError(
                f"mn_top_n ({self.mn_top_n}) must be greater than "
                f"mn_max_links ({self.mn_max_links})."
            )
        return self
