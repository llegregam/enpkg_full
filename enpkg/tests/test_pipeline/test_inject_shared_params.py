"""Tests for folding one shared ``general_params`` dict into every block section.

``GeneralParams`` describes the run, not the block, so it is collected once and merged
into each section that declares it. Every front end (the GUI and the command line) must
go through the same function: a caller that skipped it would build each block from its
Pydantic defaults, silently processing a negative-mode dataset as positive.
"""

import pytest

from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import BLOCKS, BLOCKS_BY_ID
from enpkg.monolith.pipeline.config_io import SHARED_FIELD, inject_shared_params

_SHARED = {"recompute": True, "ionization_mode": "neg"}

# MSEnhancerConfig requires a database path; every other block validates from an empty
# section once the shared params are injected.
_MINIMAL = {"ms1": {"duckdb_path": "enpkg.duckdb"}, "ms2": {"duckdb_path": "enpkg.duckdb"}}

_BLOCKS_WITH_SHARED = [
    b.id for b in BLOCKS
    if b.config_cls is not None and SHARED_FIELD in b.config_cls.model_fields
]
_BLOCKS_WITHOUT_SHARED = [b.id for b in BLOCKS if b.config_cls is None]


@pytest.mark.parametrize("block_id", _BLOCKS_WITH_SHARED)
def test_shared_params_reach_every_accepting_block(block_id):
    merged = inject_shared_params({block_id: dict(_MINIMAL.get(block_id, {}))}, _SHARED)
    assert merged[block_id][SHARED_FIELD] == _SHARED


@pytest.mark.parametrize("block_id", _BLOCKS_WITHOUT_SHARED)
def test_config_less_blocks_are_left_untouched(block_id):
    merged = inject_shared_params({block_id: {}}, _SHARED)
    assert SHARED_FIELD not in merged[block_id]


def test_input_mapping_is_not_mutated():
    raw = {"network": {}}
    inject_shared_params(raw, _SHARED)
    assert raw == {"network": {}}


def test_existing_section_values_survive_the_merge():
    raw = {"network": {"mn_top_n": 42}}
    merged = inject_shared_params(raw, _SHARED)
    assert merged["network"]["mn_top_n"] == 42
    assert merged["network"][SHARED_FIELD] == _SHARED


def test_injected_params_survive_validation():
    """The merged dict must still validate, and carry the shared values through."""
    selected = _BLOCKS_WITH_SHARED
    raw = {bid: dict(_MINIMAL.get(bid, {})) for bid in selected}
    configs = config_io.build_configs(selected, inject_shared_params(raw, _SHARED))
    for block_id in selected:
        general = configs[block_id].general_params
        assert general.ionization_mode == "neg"
        assert general.recompute is True


def test_every_block_config_declares_shared_params_or_has_none():
    """A block config that takes GeneralParams under a different name would slip through.

    ``inject_shared_params`` keys off the field name, so this pins the assumption that
    the shared sub-config is always called ``general_params``.
    """
    for block in BLOCKS:
        if block.config_cls is None:
            continue
        fields = block.config_cls.model_fields
        assert SHARED_FIELD in fields or "GeneralParams" not in str(fields), (
            f"{block.id} carries GeneralParams under an unexpected field name"
        )


def test_unknown_block_id_raises():
    """A section for a block that no longer exists is a caller bug, not silent data loss."""
    with pytest.raises(KeyError):
        inject_shared_params({"no_such_block": {}}, _SHARED)
    assert "no_such_block" not in BLOCKS_BY_ID
