"""Integrity tests for the block registry (the single source of truth)."""

from enpkg.monolith.pipeline.blocks import (
    BLOCKS,
    BLOCKS_BY_ID,
    KNOWN_RESOURCES,
    MS_SHARED_BLOCKS,
    required_resources,
)


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


# --- shared resources ------------------------------------------------------

def test_every_declared_resource_is_known():
    for block in BLOCKS:
        unknown = block.requires - KNOWN_RESOURCES
        assert not unknown, f"{block.id} requires unknown resource(s) {unknown}"


def test_required_resources_is_the_union_of_the_selection():
    assert required_resources(["ms1", "ms2"]) == {"db_loader", "lotus_store"}
    assert required_resources(["taxonomical", "network", "sirius"]) == frozenset()


def test_required_resources_of_nothing_is_empty():
    assert required_resources([]) == frozenset()


def test_required_resources_ignores_unknown_ids():
    assert required_resources(["not_a_block"]) == frozenset()
    assert required_resources(["ms1", "not_a_block"]) == {"db_loader", "lotus_store"}


def test_lotus_store_always_implies_db_loader():
    # The store reads the DuckDB file the loader manages, so a block asking for
    # the store without the loader would be built against a half-set-up run.
    for block in BLOCKS:
        if "lotus_store" in block.requires:
            assert "db_loader" in block.requires, (
                f"{block.id} requires lotus_store without db_loader"
            )


def test_blocks_needing_database_access_declare_it():
    for block_id in ("ms1", "ms2", "weights"):
        assert BLOCKS_BY_ID[block_id].requires == {"db_loader", "lotus_store"}
