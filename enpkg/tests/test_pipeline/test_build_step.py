"""Tests for binding registry blocks into runnable steps (R-08)."""

import logging

import pytest

from enpkg.monolith.pipeline.blocks import BLOCKS_BY_ID
from enpkg.monolith.pipeline.runner import _BoundBlock, _build_step


@pytest.fixture
def logger():
    lg = logging.getLogger("test.build")
    lg.addHandler(logging.NullHandler())
    return lg


def test_every_registry_block_binds(logger):
    for block_id in BLOCKS_BY_ID:
        step = _build_step(block_id, None, logger, None, None)
        assert isinstance(step, _BoundBlock)
        assert step.name() == block_id
        assert callable(step.can_run) and callable(step.process)


def test_unknown_block_raises(logger):
    with pytest.raises(KeyError):
        _build_step("does_not_exist", None, logger, None, None)


def test_can_run_guards(make_analysis, logger):
    analysis = make_analysis(n_spectra=2, source_taxon="Artemisia annua")
    assert _build_step("ms1", None, logger, None, None).can_run(analysis) is True
    assert _build_step("taxonomical", None, logger, None, None).can_run(analysis) is True
    # weights requires a molecular network, which this analysis lacks
    assert _build_step("weights", None, logger, None, None).can_run(analysis) is False


def test_taxonomical_can_run_false_without_source_taxon(make_analysis, logger):
    analysis = make_analysis(source_taxon=None)
    assert _build_step("taxonomical", None, logger, None, None).can_run(analysis) is False
