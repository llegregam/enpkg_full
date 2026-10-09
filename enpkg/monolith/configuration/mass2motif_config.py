from pydantic import Field

from enpkg.monolith.configuration.config import EnhancerConfig


class Mass2MotifConfig(EnhancerConfig):
    """Configuration for the Mass2MotifEnhancer."""

    model_dir: str = Field(
        ...,
        description="Model folder written by `enpkg motifs fit`. Its motif memberships "
        "are attached to the features of every experiment the model was fitted on.",
    )
    min_probability: float = Field(
        0.1,
        ge=0.01,
        le=1.0,
        description="Minimum document-topic probability (theta): the share of a feature's "
        "spectrum the motif must explain for the membership to be attached. The fit stores "
        "memberships above 0.01, MS2LDA's own reporting floor.",
    )
    min_overlap: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Minimum overlap score: how much of the motif's fragment and loss set "
        "the feature's spectrum must contain for the membership to be attached.",
    )
