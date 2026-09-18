"""Tests for the config-loading path every execution command shares.

Two behaviours here are worth pinning because getting them wrong is silent rather than
loud: the shared ``general_params`` must reach every block, and an ionization mode given
on the command line must not be allowed to disagree with the one in the config.
"""

import pytest
import typer
import yaml

from enpkg.cli._common import load_run_config, resolve_ionization_mode
from enpkg.monolith.pipeline import config_io

_DUCKDB = {"duckdb_path": "enpkg.duckdb"}


def _write(tmp_path, document) -> "object":
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return path


def test_shared_params_reach_every_block(tmp_path):
    """A block whose section omits general_params must still get the run's mode.

    Without the merge each block would fall back to its Pydantic default of "pos" and a
    negative-mode dataset would be processed as positive, with nothing to show for it.
    """
    path = _write(
        tmp_path,
        {
            config_io.SELECTION_KEY: ["network", "ms1"],
            "network": {"general_params": {"ionization_mode": "neg", "recompute": True}},
            "ms_enhancer": dict(_DUCKDB),
        },
    )
    _, configs, _ = load_run_config(path)
    assert configs["ms1"].general_params.ionization_mode == "neg"
    assert configs["network"].general_params.ionization_mode == "neg"


def test_serializer_section_is_loaded(tmp_path):
    path = _write(
        tmp_path,
        {
            config_io.SELECTION_KEY: ["network"],
            "network": {},
            config_io.SERIALIZER_KEY: {"top_k_ms1": 2, "include_ions": True},
        },
    )
    _, _, serializer_config = load_run_config(path)
    assert serializer_config.top_k_ms1 == 2
    assert serializer_config.include_ions is True


def test_absent_serializer_section_yields_defaults(tmp_path):
    path = _write(tmp_path, {config_io.SELECTION_KEY: ["network"], "network": {}})
    _, _, serializer_config = load_run_config(path)
    assert serializer_config.top_k_ms1 == 5


@pytest.mark.parametrize(
    "document",
    [
        {"network": {}},  # no recorded selection
        {config_io.SELECTION_KEY: []},  # an empty one
        {config_io.SELECTION_KEY: ["no_such_block"]},  # an unknown id
    ],
    ids=["no-selection", "empty-selection", "unknown-block"],
)
def test_unusable_selections_are_refused(tmp_path, document):
    with pytest.raises(typer.Exit):
        load_run_config(_write(tmp_path, document))


def test_invalid_section_is_refused(tmp_path):
    path = _write(
        tmp_path,
        {config_io.SELECTION_KEY: ["network"], "network": {"mn_top_n": "not a number"}},
    )
    with pytest.raises(typer.Exit):
        load_run_config(path)


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(typer.Exit):
        load_run_config(tmp_path / "absent.yaml")


def _configs_with_mode(mode: str):
    from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig

    return {"network": NetworkEnhancerConfig(general_params={"ionization_mode": mode})}


def test_mode_comes_from_the_config_when_not_overridden():
    assert resolve_ionization_mode(_configs_with_mode("neg"), None) == "neg"


def test_matching_override_is_accepted():
    assert resolve_ionization_mode(_configs_with_mode("neg"), "neg") == "neg"


def test_contradicting_override_is_refused():
    """The mode is both a runner argument and a config field, and nothing reconciles them.

    An override that disagreed would decide how the analysis is loaded while every block
    kept the config's value -- a run that is half one mode and half the other.
    """
    with pytest.raises(typer.Exit):
        resolve_ionization_mode(_configs_with_mode("neg"), "pos")


def test_override_applies_when_no_config_carries_the_field():
    assert resolve_ionization_mode({"taxonomical": None}, "neg") == "neg"
