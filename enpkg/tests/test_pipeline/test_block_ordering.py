"""Tests for deriving execution order from block ordering constraints.

``BLOCKS`` is the output of ``order_blocks`` rather than a hand-written
sequence, so these cover both the algorithm and the order it produces for the
built-in registry.
"""

import pytest

from enpkg.monolith.pipeline.blocks import (
    BLOCKS,
    BlockOrderError,
    BlockSpec,
    order_blocks,
)

# The order the pipeline has always run in. Pinned here so a change to any
# block's constraints that would reshuffle the run fails loudly.
CANONICAL_ORDER = [
    "taxonomical",
    "network",
    "ms1_graph",
    "ms1",
    "ms2",
    "sirius",
    "weights",
]


def _spec(block_id: str, **kwargs) -> BlockSpec:
    """A BlockSpec carrying only what the ordering code reads."""
    return BlockSpec(
        id=block_id,
        label=block_id,
        build_enhancer=lambda config, ctx: None,
        can_run=lambda analysis: True,
        config_cls=None,
        log_summary=lambda logger, analysis: None,
        **kwargs,
    )


def _ids(specs) -> list[str]:
    return [s.id for s in specs]


# --- the built-in registry -------------------------------------------------

def test_builtin_blocks_are_in_canonical_order():
    assert _ids(BLOCKS) == CANONICAL_ORDER


def test_every_builtin_constraint_holds_in_the_computed_order():
    position = {spec.id: i for i, spec in enumerate(BLOCKS)}
    for spec in BLOCKS:
        for earlier in spec.after:
            assert position[earlier] < position[spec.id], (
                f"{spec.id} declares after={earlier!r} but runs before it"
            )
        for later in spec.before:
            assert position[spec.id] < position[later], (
                f"{spec.id} declares before={later!r} but runs after it"
            )


def test_ms1_graph_runs_before_ms1():
    # The MS1 enhancer reads the cluster roles the graph block stamps.
    assert _ids(BLOCKS).index("ms1_graph") < _ids(BLOCKS).index("ms1")


def test_weights_runs_after_everything_it_reranks():
    order = _ids(BLOCKS)
    for produced_first in ("network", "ms1", "ms2"):
        assert order.index(produced_first) < order.index("weights")


# --- the algorithm ---------------------------------------------------------

def test_unconstrained_blocks_keep_declaration_order():
    specs = [_spec("c"), _spec("a"), _spec("b")]
    assert _ids(order_blocks(specs)) == ["c", "a", "b"]


def test_after_moves_a_block_later():
    specs = [_spec("a", after=("b",)), _spec("b")]
    assert _ids(order_blocks(specs)) == ["b", "a"]


def test_before_moves_a_block_earlier():
    specs = [_spec("a"), _spec("b", before=("a",))]
    assert _ids(order_blocks(specs)) == ["b", "a"]


def test_after_and_before_agree_on_the_same_edge():
    # Both sides declaring the same relationship must not double-count it.
    specs = [_spec("a", after=("b",)), _spec("b", before=("a",))]
    assert _ids(order_blocks(specs)) == ["b", "a"]


def test_constraint_naming_an_absent_block_is_ignored():
    # A plugin may order against a block that is not installed.
    specs = [_spec("a", after=("not_installed",)), _spec("b")]
    assert _ids(order_blocks(specs)) == ["a", "b"]


def test_ordering_is_independent_of_collection_order():
    # Same blocks, different declaration order: constraints still hold, and the
    # unconstrained pair resolves by declaration position each time.
    constrained = [_spec("x"), _spec("y", after=("x",))]
    reversed_decl = [_spec("y", after=("x",)), _spec("x")]
    assert _ids(order_blocks(constrained)) == ["x", "y"]
    assert _ids(order_blocks(reversed_decl)) == ["x", "y"]


def test_transitive_chain_resolves():
    specs = [_spec("c", after=("b",)), _spec("b", after=("a",)), _spec("a")]
    assert _ids(order_blocks(specs)) == ["a", "b", "c"]


def test_empty_registry_is_allowed():
    assert order_blocks([]) == []


def test_cycle_raises_and_names_the_blocks():
    specs = [_spec("a", after=("b",)), _spec("b", after=("a",))]
    with pytest.raises(BlockOrderError) as excinfo:
        order_blocks(specs)
    message = str(excinfo.value)
    assert "a" in message and "b" in message


def test_self_reference_is_a_cycle():
    with pytest.raises(BlockOrderError):
        order_blocks([_spec("a", after=("a",))])
