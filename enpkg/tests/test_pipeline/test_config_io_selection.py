"""Tests for the block-selection round trip through the unified YAML.

The selection cannot be recovered from the section names alone -- taxonomical
has no ``config_cls`` and so never writes a section, and MS1/MS2 share one
``ms_enhancer`` section -- so ``save_unified_yaml`` records it explicitly under
``selected_blocks``. These tests pin that contract down, because a load is a
full replace: if the selection were lost, loading a narrow config file would
reset the other blocks' parameters while leaving them ticked and about to run.
"""

import pytest
import yaml

from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import BLOCKS_BY_ID
from enpkg.monolith.pipeline.config_io import SELECTION_KEY

# MSEnhancerConfig requires a database path, so the MS blocks cannot be built
# from an empty form. Every other block's section stays empty.
_MINIMAL_FORM_STATE = {"ms1": {"duckdb_path": "enpkg.duckdb"},
                       "ms2": {"duckdb_path": "enpkg.duckdb"}}


def _configs(selected_ids):
    """Build the ``{block_id: config|None}`` mapping the app hands to save."""
    return config_io.build_configs(list(selected_ids), _MINIMAL_FORM_STATE)


def test_save_records_config_less_block(tmp_path):
    """taxonomical has no section of its own but must survive the round trip."""
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(path, _configs(["taxonomical", "network"]))

    doc = yaml.safe_load(path.read_text())
    assert doc[SELECTION_KEY] == ["taxonomical", "network"]
    assert "taxonomical" not in doc, "config-less block should not emit a section"
    assert config_io.get_selection(doc) == ["taxonomical", "network"]


def test_save_distinguishes_ms1_from_ms2(tmp_path):
    """MS1 and MS2 dedupe into one section, so only the list can tell them apart."""
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(path, _configs(["ms1"]))

    doc = yaml.safe_load(path.read_text())
    assert doc[SELECTION_KEY] == ["ms1"]
    assert "ms_enhancer" in doc and "ms2" not in doc
    assert config_io.get_selection(doc) == ["ms1"]


def test_selection_is_in_registry_order(tmp_path):
    """Order follows BLOCKS, not the caller's, so files diff cleanly."""
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(path, _configs(["weights", "network", "taxonomical"]))

    assert yaml.safe_load(path.read_text())[SELECTION_KEY] == [
        "taxonomical",
        "network",
        "weights",
    ]


def test_round_trip_of_every_block(tmp_path):
    """Every registered block, selected at once, comes back intact."""
    path = tmp_path / "config.yaml"
    all_ids = list(BLOCKS_BY_ID)
    config_io.save_unified_yaml(path, _configs(all_ids))

    assert config_io.get_selection(config_io.load_unified_yaml(path)) == all_ids


def test_missing_key_is_none_not_empty():
    """A pre-selected_blocks file must be distinguishable from 'nothing selected'.

    ``None`` tells the app to leave the checkboxes alone; ``[]`` tells it to
    untick everything.
    """
    assert config_io.get_selection({"network": {}}) is None
    assert config_io.get_selection({SELECTION_KEY: []}) == []


def test_unknown_block_id_is_dropped():
    """A block removed from the registry must not break an old config file."""
    assert config_io.get_selection(
        {SELECTION_KEY: ["network", "block_from_a_past_version"]}
    ) == ["network"]


def test_non_list_selection_is_rejected():
    with pytest.raises(ValueError, match=SELECTION_KEY):
        config_io.get_selection({SELECTION_KEY: "network"})


def test_selection_does_not_shadow_a_section():
    """SELECTION_KEY must never collide with a real block id."""
    assert SELECTION_KEY not in BLOCKS_BY_ID
    assert config_io.get_section({SELECTION_KEY: ["network"]}, "network") == {}
