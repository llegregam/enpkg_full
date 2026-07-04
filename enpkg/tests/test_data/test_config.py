"""Unit tests for configuration validation."""

import pytest
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.MSEnhancer_config import (
    MSEnhancerConfig,
    SpectralMatchParams,
)


def test_polarity_accepts_pos_and_neg():
    assert GeneralParams(polarity="pos").polarity == "pos"
    assert GeneralParams(polarity="neg").polarity == "neg"


def test_polarity_rejects_invalid():
    with pytest.raises(ValidationError):
        GeneralParams(polarity="positive")


@pytest.mark.parametrize(
    "field, value",
    [
        ("parent_mz_tol", 0.0),
        ("parent_mz_tol", -1.0),
        ("msms_mz_tol", 0.0),
        ("min_score", -0.1),
        ("min_score", 1.1),
        ("min_peaks", 0),
    ],
)
def test_spectral_match_param_bounds(field, value):
    with pytest.raises(ValidationError):
        SpectralMatchParams(**{field: value})


def test_method_must_be_a_known_similarity():
    assert SpectralMatchParams(method="cosine_hungarian").method == "cosine_hungarian"
    with pytest.raises(ValidationError):
        SpectralMatchParams(method="euclidean")


def test_enhancer_config_forbids_unknown_keys():
    with pytest.raises(ValidationError):
        MSEnhancerConfig.from_dict({"nonexistent_key": 1})


def test_from_dict_roundtrip():
    cfg = MSEnhancerConfig.from_dict(
        {
            "general_params": {"polarity": "neg"},
            "spectral_match_params": {"min_peaks": 3, "method": "cosine_hungarian"},
        }
    )
    assert cfg.general_params.polarity == "neg"
    assert cfg.spectral_match_params.min_peaks == 3
    assert cfg.spectral_match_params.method == "cosine_hungarian"
