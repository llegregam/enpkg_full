"""Tests for the ``serializer`` section of the unified config YAML.

``serializer`` is a top-level key rather than a block id: the Turtle export runs after
the block loop, so it never appears in ``selected_blocks`` and is never skipped. It has
to survive a save/load round trip without disturbing the block sections, and a file
written without it has to stay readable.
"""

import yaml

from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.config_io import SELECTION_KEY, SERIALIZER_KEY

_MINIMAL = {"ms1": {"duckdb_path": "enpkg.duckdb"}, "ms2": {"duckdb_path": "enpkg.duckdb"}}


def _configs(selected):
    return config_io.build_configs(
        selected, {bid: dict(_MINIMAL.get(bid, {})) for bid in selected}
    )


def test_serializer_section_round_trips(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = SerializerConfig(include_ions=True, top_k_ms1=3, max_ions_per_spectrum=20)
    config_io.save_unified_yaml(path, _configs(["network"]), serializer=cfg)

    data = config_io.load_unified_yaml(path)
    restored = SerializerConfig.model_validate(config_io.get_serializer_section(data))
    assert restored == cfg


def test_absent_section_yields_defaults(tmp_path):
    """A file written before the section existed must still load."""
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(path, _configs(["network"]))

    data = config_io.load_unified_yaml(path)
    assert config_io.get_serializer_section(data) == {}
    assert SerializerConfig.model_validate({}) == SerializerConfig()


def test_serializer_key_is_not_treated_as_a_block(tmp_path):
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(path, _configs(["network"]), serializer=SerializerConfig())

    data = config_io.load_unified_yaml(path)
    assert SERIALIZER_KEY not in data[SELECTION_KEY]
    assert config_io.get_selection(data) == ["network"]


def test_unset_optional_is_written_not_dropped(tmp_path):
    """``max_ions_per_spectrum: null`` must be visible, not absent.

    An absent key leaves a reader unable to tell "no cap was chosen" from "this file
    predates the option".
    """
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(path, _configs(["network"]), serializer=SerializerConfig())

    raw = yaml.safe_load(path.read_text())
    assert "max_ions_per_spectrum" in raw[SERIALIZER_KEY]
    assert raw[SERIALIZER_KEY]["max_ions_per_spectrum"] is None


def test_block_sections_are_unaffected(tmp_path):
    path = tmp_path / "config.yaml"
    config_io.save_unified_yaml(
        path, _configs(["network", "ms1", "ms2"]), serializer=SerializerConfig()
    )

    data = config_io.load_unified_yaml(path)
    assert config_io.get_section(data, "ms1") == config_io.get_section(data, "ms2")
    assert "mn_top_n" in config_io.get_section(data, "network")
