"""
Abstract configuration class for the enhancers.
"""
from abc import ABC
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
import yaml


class GeneralParams(BaseModel):
    """General processing parameters."""

    recompute: bool = Field(
        default=False,
        description="Whether to recompute results even if they already exist"
    )
    
    polarity: str = Field(
        default="pos",
        pattern="^(pos|neg)$",
        description="Ionization mode polarity ('pos' or 'neg')"
    )

class EnhancerConfig(BaseModel, ABC):
    """Interface for building enhancer configurations."""

    # Allows using extra fields or forbidden them for strictness
    model_config = ConfigDict(extra='forbid')

    general_params: GeneralParams = Field(
        default_factory=GeneralParams,
        description="General processing parameters"
    )

    @classmethod
    def from_yaml(cls, path: str):
        with open(path, "r") as f:
            config_dict = yaml.safe_load(f)
        # Use model_validate to leverage Pydantic's parsing logic
        return cls.model_validate(config_dict)
    
    @classmethod
    def from_dict(cls, config_dict: dict):
        return cls.model_validate(config_dict)


