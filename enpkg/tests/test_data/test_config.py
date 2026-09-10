"""Unit tests for configuration validation."""

import pytest
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.MSEnhancer_config import (
    MSEnhancerConfig,
    SpectralMatchParams,
)


def test_ionization_mode_accepts_pos_and_neg():
    assert GeneralParams(ionization_mode="pos").ionization_mode == "pos"
    assert GeneralParams(ionization_mode="neg").ionization_mode == "neg"


def test_ionization_mode_rejects_invalid():
    with pytest.raises(ValidationError):
        GeneralParams(ionization_mode="positive")


def test_ionization_mode_accepts_legacy_polarity_key():
    """``polarity`` was the field's old name; saved YAML configs on disk still use
    it, so it must keep working rather than silently falling back to the default."""
    assert GeneralParams(polarity="neg").ionization_mode == "neg"


def test_ionization_mode_prefers_new_key_when_both_given():
    assert GeneralParams(polarity="neg", ionization_mode="pos").ionization_mode == "pos"


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
            "duckdb_path": "enpkg.duckdb",
            "general_params": {"ionization_mode": "neg"},
            "spectral_match_params": {"min_peaks": 3, "method": "cosine_hungarian"},
        }
    )
    assert cfg.general_params.ionization_mode == "neg"
    assert cfg.spectral_match_params.min_peaks == 3
    assert cfg.spectral_match_params.method == "cosine_hungarian"


def test_from_dict_roundtrip_legacy_polarity_key():
    """A saved config on disk with the old ``polarity`` key must still load
    correctly through a full EnhancerConfig (not just GeneralParams directly)."""
    cfg = MSEnhancerConfig.from_dict(
        {
            "duckdb_path": "enpkg.duckdb",
            "general_params": {"polarity": "neg"},
            "spectral_match_params": {"min_peaks": 3, "method": "cosine_hungarian"},
        }
    )
    assert cfg.general_params.ionization_mode == "neg"
