"""Orchestration tests for the runner's block-execution core (_run_analysis).

These pin the stable contract — iterate the registry in canonical order, honour
can_run / dependency / missing-step skips, and stop on error — using duck-typed
stub steps, so they hold regardless of how individual steps are built.
"""

import logging

from enpkg.monolith.pipeline.runner import RunResult, _run_analysis


class _StubStep:
    def __init__(self, can_run: bool = True, fail: bool = False):
        self._can_run = can_run
        self._fail = fail
        self.processed = False

    def can_run(self, analysis):
        return self._can_run

    def process(self, analysis):
        self.processed = True
        if self._fail:
            raise RuntimeError("boom")
        return analysis


def _run(make_analysis, selected, steps):
    logger = logging.getLogger("test.runner")
    logger.addHandler(logging.NullHandler())
    summary = logging.getLogger("test.summary")
    summary.addHandler(logging.NullHandler())
    return _run_analysis(make_analysis(), selected, steps, logger, summary, RunResult())


def test_executes_selected_blocks_in_registry_order(make_analysis):
    # Registry order puts ms1 before ms2, regardless of the selected-id order.
    steps = {"ms1": _StubStep(), "ms2": _StubStep()}
    result = _run(make_analysis, ["ms2", "ms1"], steps)
    assert result.executed == ["ms1", "ms2"]
    assert result.error is None


def test_skips_block_when_can_run_is_false(make_analysis):
    steps = {"ms1": _StubStep(can_run=False)}
    result = _run(make_analysis, ["ms1"], steps)
    assert result.executed == []
    assert "ms1" in result.skipped
    assert steps["ms1"].processed is False


def test_skips_block_with_missing_dependency(make_analysis):
    # weights depends on network; selecting weights alone must skip it.
    steps = {"weights": _StubStep()}
    result = _run(make_analysis, ["weights"], steps)
    assert result.executed == []
    assert "weights" in result.skipped


def test_skips_block_with_no_step_instance(make_analysis):
    result = _run(make_analysis, ["ms1"], {})
    assert "ms1" in result.skipped


def test_records_error_and_halts_on_step_exception(make_analysis):
    steps = {"ms1": _StubStep(fail=True), "ms2": _StubStep()}
    result = _run(make_analysis, ["ms1", "ms2"], steps)
    assert result.error is not None and "ms1" in result.error
    assert "ms2" not in result.executed
    assert steps["ms2"].processed is False
