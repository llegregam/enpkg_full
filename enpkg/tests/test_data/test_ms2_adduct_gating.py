"""Unit tests for MS2 adduct gating (which features the MS2 enhancer annotates).

Pure/fast: exercises ``select_spectra_for_ms2`` directly on synthetic spectra
stamped with MS1 cluster roles — no matchms, DB, or network involved.
"""

import logging

import pytest

from enpkg.monolith.utils.ms2_adduct_gating import (
    MS2_ADDUCT_FILTER_MODES,
    select_spectra_for_ms2,
)

_LOGGER = logging.getLogger("test_ms2_adduct_gating")


def _stamped(make_spectrum):
    """One spectrum per role, stamped as the MS1 graph enhancer would."""
    anchor = make_spectrum(feature_id=1)
    anchor.ms1_cluster_role = "anchor"  # base ion [M+H]+
    satellite = make_spectrum(feature_id=2)
    satellite.ms1_cluster_role = "satellite"  # e.g. [M+Na]+
    singleton = make_spectrum(feature_id=3)
    singleton.ms1_cluster_role = "singleton"  # no adduct info
    return [anchor, satellite, singleton]


def _ids(spectra):
    return {s.feature_id for s in spectra}


def test_non_satellite_keeps_anchor_and_singleton(make_spectrum):
    selected = select_spectra_for_ms2(_stamped(make_spectrum), "non_satellite", _LOGGER)
    assert _ids(selected) == {1, 3}  # satellite (2) dropped


def test_base_only_keeps_only_anchor(make_spectrum):
    selected = select_spectra_for_ms2(_stamped(make_spectrum), "base_only", _LOGGER)
    assert _ids(selected) == {1}


def test_all_keeps_everything(make_spectrum):
    selected = select_spectra_for_ms2(_stamped(make_spectrum), "all", _LOGGER)
    assert _ids(selected) == {1, 2, 3}


def test_falls_back_to_all_when_no_roles_stamped(make_spectrum):
    # Graph never ran: every role is None -> gating impossible, annotate all.
    spectra = [make_spectrum(feature_id=i) for i in (1, 2, 3)]
    assert all(s.ms1_cluster_role is None for s in spectra)
    selected = select_spectra_for_ms2(spectra, "base_only", _LOGGER)
    assert _ids(selected) == {1, 2, 3}


def test_partial_roles_still_gate(make_spectrum):
    # At least one role stamped -> gate (an un-stamped feature is treated as
    # non-satellite and kept under the default mode).
    a = make_spectrum(feature_id=1)
    a.ms1_cluster_role = "satellite"
    b = make_spectrum(feature_id=2)  # role None
    selected = select_spectra_for_ms2([a, b], "non_satellite", _LOGGER)
    assert _ids(selected) == {2}


def test_unknown_mode_raises(make_spectrum):
    with pytest.raises(ValueError, match="Unknown ms2_adduct_filter mode"):
        select_spectra_for_ms2(_stamped(make_spectrum), "bogus", _LOGGER)


def test_default_mode_is_a_known_mode():
    # Guard: the config default must be one of the recognised modes.
    from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig

    assert MSEnhancerConfig().ms2_adduct_filter in MS2_ADDUCT_FILTER_MODES
