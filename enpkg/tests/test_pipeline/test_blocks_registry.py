"""Integrity tests for the block registry (the single source of truth)."""

from enpkg.monolith.gui.blocks import BLOCKS, BLOCKS_BY_ID, MS_SHARED_BLOCKS


def test_block_ids_are_unique():
    ids = [b.id for b in BLOCKS]
    assert len(ids) == len(set(ids))


def test_blocks_by_id_is_consistent():
    assert set(BLOCKS_BY_ID) == {b.id for b in BLOCKS}
    for block_id, spec in BLOCKS_BY_ID.items():
        assert spec.id == block_id


def test_dependencies_reference_real_blocks():
    ids = {b.id for b in BLOCKS}
    for block in BLOCKS:
        for dep in block.depends_on:
            assert dep in ids, f"{block.id} depends on unknown block {dep!r}"


def test_weights_depends_on_network():
    assert "network" in BLOCKS_BY_ID["weights"].depends_on


def test_every_block_has_a_callable_log_summary():
    for block in BLOCKS:
        assert callable(block.log_summary)


def test_ms_shared_blocks_exist():
    for block_id in MS_SHARED_BLOCKS:
        assert block_id in BLOCKS_BY_ID
