"""Attaching training-run motif memberships from a model folder to an Analysis."""

import logging

import pytest
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.mass2motif_config import Mass2MotifConfig
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.data.mass2motif import MotifMembership
from enpkg.monolith.enhancers.mass2motif_enhancer import Mass2MotifEnhancer
from enpkg.monolith.loaders.mass2motif_model import Mass2MotifModel
from enpkg.tests.test_motifs.model_folders import DEFAULT_DOCUMENTS, write_model_folder

LOGGER = logging.getLogger("test_mass2motif")


def _enhance(analysis, folder, **config):
    enhancer = Mass2MotifEnhancer(Mass2MotifConfig(model_dir=str(folder), **config), LOGGER)
    return enhancer.enhance(analysis)


def _memberships(analysis):
    return {s.feature_id: s.motif_memberships for s in analysis.spectra}


def test_memberships_above_the_threshold_are_attached_highest_probability_first(
    make_analysis, model_folder
):
    result = _enhance(make_analysis(n_spectra=3), model_folder, min_probability=0.01)
    assert _memberships(result) == {
        1: [MotifMembership(0, 0.5, 0.7), MotifMembership(1, 0.05, 0.1)],
        2: [MotifMembership(1, 0.2, 0.0)],
        3: [],
    }
    assert result.motif_set == Mass2MotifModel.load(model_folder).motif_set


def test_the_default_threshold_is_a_probability_of_0_1(make_analysis, model_folder):
    result = _enhance(make_analysis(n_spectra=3), model_folder)
    assert _memberships(result)[1] == [MotifMembership(0, 0.5, 0.7)]


def test_the_overlap_threshold_applies_too(make_analysis, model_folder):
    result = _enhance(make_analysis(n_spectra=3), model_folder, min_overlap=0.5)
    assert _memberships(result) == {1: [MotifMembership(0, 0.5, 0.7)], 2: [], 3: []}


def test_a_run_outside_the_fit_is_left_unchanged(make_analysis, model_folder, caplog):
    analysis = make_analysis(run_name="ELSEWHERE", n_spectra=3)
    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        result = _enhance(analysis, model_folder)
    assert result.motif_set is None
    assert all(not s.motif_memberships for s in result.spectra)
    assert "not among the experiments" in caplog.text


def test_a_model_of_the_other_ionization_mode_is_not_applied(make_analysis, tmp_path, caplog):
    folder = write_model_folder(tmp_path / "neg", ionization_mode="neg")
    with caplog.at_level(logging.WARNING, logger=LOGGER.name):
        result = _enhance(make_analysis(n_spectra=3), folder)
    assert result.motif_set is None
    assert "neg-mode" in caplog.text


def test_a_model_of_the_run_mode_is_applied_in_negative_mode(make_analysis, tmp_path):
    folder = write_model_folder(tmp_path / "neg", ionization_mode="neg")
    enhancer = Mass2MotifEnhancer(
        Mass2MotifConfig(model_dir=str(folder), general_params=GeneralParams(ionization_mode="neg")),
        LOGGER,
    )
    result = enhancer.enhance(make_analysis(n_spectra=3, ionization_mode="neg"))
    assert result.motif_set is not None


def test_a_changed_precursor_mz_stops_the_block(make_analysis, tmp_path):
    documents = [row if row[2] != "2" else (row[0], row[1], row[2], 250.0) for row in DEFAULT_DOCUMENTS]
    folder = write_model_folder(tmp_path / "m", documents=documents)
    with pytest.raises(ValueError, match="changed after the fit"):
        _enhance(make_analysis(n_spectra=3), folder)


def test_a_repeated_feature_id_stops_the_block(make_analysis, tmp_path):
    documents = DEFAULT_DOCUMENTS + [("spec_5", "RUN1", "1", 200.0)]
    folder = write_model_folder(tmp_path / "m", documents=documents)
    with pytest.raises(ValueError, match="several spectra"):
        _enhance(make_analysis(n_spectra=3), folder)


def test_the_enhancer_rejects_another_config_type():
    with pytest.raises(TypeError):
        Mass2MotifEnhancer(NetworkEnhancerConfig(), LOGGER)


def test_a_probability_threshold_below_the_stored_floor_is_rejected(model_folder):
    with pytest.raises(ValidationError):
        Mass2MotifConfig(model_dir=str(model_folder), min_probability=0.001)


def test_the_model_folder_is_required():
    with pytest.raises(ValidationError):
        Mass2MotifConfig()
