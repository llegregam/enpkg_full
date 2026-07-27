"""Tests for the MS1 graph enhancer (no database needed — pure graph resolution)."""

import logging

import pytest

from enpkg.monolith.configuration.ms1_graph_enhancer_config import MS1GraphEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.enhancers.ms1_graph_enhancer import MS1GraphEnhancer

_LOGGER = logging.getLogger("test_ms1_graph_enhancer")

# C9H8O4, exact mass 180.04226 — the compound from docs/MS1_ADDUCT_RANKING_ISSUE.md.
_MH = 181.0495  # [M+H]+  (feature 543)
_MNA = 203.0320  # [M+Na]+
_MK = 219.0060  # [M+K]+


def _analysis(make_spectrum) -> Analysis:
    spectra = (
        make_spectrum(feature_id=543, precursor_mz=_MH, retention_time=3.458, intensity=2.66e4),
        make_spectrum(feature_id=100, precursor_mz=_MNA, retention_time=3.458, intensity=5.0e3),
        make_spectrum(feature_id=101, precursor_mz=_MK, retention_time=3.458, intensity=3.0e3),
        # Unrelated, far in both m/z and RT.
        make_spectrum(feature_id=200, precursor_mz=400.0, retention_time=8.0, intensity=1.0e3),
    )
    return Analysis(
        run_name="RUN1",
        spectra=spectra,
        metadata=SampleMetadata(sample_id="S1", source_taxon="Actaea"),
        ionization_mode="pos",
    )


def test_feature_543_resolves_as_protonated_anchor(make_spectrum):
    """Regression for the MS1 ranking bug: the co-eluting [M+Na]+/[M+K]+ siblings
    corroborate C9H8O4's [M+H]+, so feature 543 anchors as [M+H]+ — not the
    implausible [M+H+2Na]3+ the old taxonomy ranker produced."""
    analysis = _analysis(make_spectrum)
    enhanced = MS1GraphEnhancer(MS1GraphEnhancerConfig(), _LOGGER).enhance(analysis)

    by_id = {s.feature_id: s for s in enhanced.spectra}
    assert by_id[543].ms1_cluster_role == "anchor"
    assert by_id[543].ms1_assigned_recipe.ingredients == {"proton": 1}
    assert by_id[543].ms1_assigned_recipe.charge == 1
    assert by_id[100].ms1_cluster_role == "satellite"
    assert by_id[100].ms1_assigned_recipe.ingredients == {"sodium": 1}
    assert by_id[101].ms1_assigned_recipe.ingredients == {"potassium": 1}


def test_unrelated_feature_is_singleton(make_spectrum):
    analysis = _analysis(make_spectrum)
    enhanced = MS1GraphEnhancer(MS1GraphEnhancerConfig(), _LOGGER).enhance(analysis)
    by_id = {s.feature_id: s for s in enhanced.spectra}
    assert by_id[200].ms1_cluster_role == "singleton"
    assert by_id[200].ms1_cluster_id is None


def test_graph_is_attached_and_covers_all_features(make_spectrum):
    analysis = _analysis(make_spectrum)
    enhanced = MS1GraphEnhancer(MS1GraphEnhancerConfig(), _LOGGER).enhance(analysis)
    assert enhanced.ms1_adduct_graph is not None
    assert set(enhanced.ms1_adduct_graph.nodes) == {543, 100, 101, 200}


def test_cluster_members_share_a_cluster_id(make_spectrum):
    analysis = _analysis(make_spectrum)
    enhanced = MS1GraphEnhancer(MS1GraphEnhancerConfig(), _LOGGER).enhance(analysis)
    by_id = {s.feature_id: s for s in enhanced.spectra}
    cluster_ids = {by_id[543].ms1_cluster_id, by_id[100].ms1_cluster_id, by_id[101].ms1_cluster_id}
    assert len(cluster_ids) == 1 and None not in cluster_ids
    assert by_id[543].ms1_cluster_connectivity == 3
    assert by_id[543].ms1_cluster_intensity_coverage == pytest.approx(
        (2.66e4 + 5.0e3 + 3.0e3) / (2.66e4 + 5.0e3 + 3.0e3 + 1.0e3)
    )
