"""Tests for reading a configuration file into the stored settings.

Loading happens on the Imports page while the widgets it fills are on the Pipeline page,
which is not built at that moment — so it writes to the store and the Pipeline page seeds
itself from there. Nothing here touches a widget, which is what makes it testable without
a page.
"""

import pytest
import yaml

from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.config_io import SELECTION_KEY
from enpkg.monolith.webui.config_files import MS_FORM_KEY, read_config

_MINIMAL_MS = {"duckdb_path": "enpkg.duckdb"}


def _write(tmp_path, selected, form_state=None, serializer=None):
    path = tmp_path / "config.yaml"
    configs = config_io.build_configs(selected, form_state or {})
    config_io.save_unified_yaml(path, configs, serializer=serializer)
    return path


def test_the_selection_is_read_back(tmp_path):
    path = _write(tmp_path, ["network", "taxonomical"])
    assert read_config(path).selected_blocks == ["taxonomical", "network"]


def test_ms1_and_ms2_share_one_form_entry(tmp_path):
    """They share a config, so the file holds one section for both."""
    path = _write(
        tmp_path, ["ms1", "ms2"], {"ms1": dict(_MINIMAL_MS), "ms2": dict(_MINIMAL_MS)}
    )
    loaded = read_config(path)
    assert loaded.selected_blocks == ["ms1", "ms2"]
    assert loaded.form_state[MS_FORM_KEY]["duckdb_path"] == "enpkg.duckdb"


def test_general_params_are_lifted_out_of_the_sections(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                SELECTION_KEY: ["network"],
                "network": {
                    "general_params": {"ionization_mode": "neg", "recompute": True}
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = read_config(path)
    assert loaded.general_params["ionization_mode"] == "neg"
    assert loaded.general_params["recompute"] is True


def test_missing_general_params_fall_back_to_defaults(tmp_path):
    path = _write(tmp_path, ["network"])
    assert read_config(path).general_params["ionization_mode"] == "pos"


def test_the_serializer_section_is_read(tmp_path):
    path = _write(tmp_path, ["network"], serializer=SerializerConfig(top_k_ms1=2))
    assert read_config(path).serializer["top_k_ms1"] == 2


def test_a_missing_serializer_section_falls_back_to_defaults(tmp_path):
    path = _write(tmp_path, ["network"])
    assert read_config(path).serializer["top_k_ms1"] == 5


def test_a_file_without_a_selection_warns_rather_than_guessing(tmp_path):
    """Section names cannot stand in for the selection, so it refuses to infer one."""
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump({"network": {}}), encoding="utf-8")

    loaded = read_config(path)
    assert loaded.selected_blocks == []
    assert loaded.warning is not None
    assert "no block selection" in loaded.warning


def test_every_configurable_block_gets_an_entry(tmp_path):
    """A block absent from the file must reset, so it needs an entry to reset to."""
    path = _write(tmp_path, ["network"])
    loaded = read_config(path)
    assert "sirius" in loaded.form_state
    assert "weights" in loaded.form_state


def test_a_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        read_config(tmp_path / "absent.yaml")


def test_a_file_that_is_not_a_mapping_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError):
        read_config(path)
