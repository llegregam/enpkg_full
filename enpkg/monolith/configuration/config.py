"""
Abstract configuration class for the enhancers.
"""
from abc import ABC

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeneralParams(BaseModel):
    """General processing parameters."""

    recompute: bool = Field(
        default=False,
        description="Whether to recompute results even if they already exist"
    )

    ionization_mode: str = Field(
        default="pos",
        pattern="^(pos|neg)$",
        description="Ionization mode ('pos' or 'neg')"
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_polarity_key(cls, data):
        """Accept the old ``polarity`` key as an alias for ``ionization_mode``.

        ``polarity`` was the field's name before it was renamed to match
        ``Analysis.ionization_mode`` (the two were always the same concept under
        different names). Saved YAML configs on disk (e.g. from the GUI) still use
        the old key, so it's translated here rather than left to silently fall
        back to the default and mis-process negative-mode runs as positive.
        """
        if isinstance(data, dict) and "polarity" in data and "ionization_mode" not in data:
            data = {**data, "ionization_mode": data["polarity"]}
            del data["polarity"]
        return data


class EnhancerConfig(BaseModel, ABC):
    """Interface for building enhancer configurations."""

    # Allows using extra fields or forbidden them for strictness
    model_config = ConfigDict(extra='forbid')

    general_params: GeneralParams = Field(
        default_factory=GeneralParams,
        description="General processing parameters"
    )

    def __repr__(self):
        return f"{self.__class__.__name__} input parameters:\n{self.model_dump_json(indent=2)}"

    @classmethod
    def from_yaml(cls, path: str):
        with open(path, "r") as f:
            config_dict = yaml.safe_load(f)
        # Use model_validate to leverage Pydantic's parsing logic
        return cls.model_validate(config_dict)

    @classmethod
    def from_dict(cls, config_dict: dict):
        return cls.model_validate(config_dict)


