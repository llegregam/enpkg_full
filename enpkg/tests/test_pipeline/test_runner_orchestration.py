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


# --- Timing instrumentation -------------------------------------------------


def test_records_a_duration_for_every_executed_block(make_analysis):
    steps = {"ms1": _StubStep(), "ms2": _StubStep()}
    result = _run(make_analysis, ["ms1", "ms2"], steps)
    assert set(result.durations) == {"ms1", "ms2"}
    assert all(value >= 0.0 for value in result.durations.values())


def test_records_duration_for_a_block_that_raises(make_analysis):
    # A step that runs a long time and then fails is exactly the one whose
    # duration is worth having, so the failure path must still record it.
    steps = {"ms1": _StubStep(fail=True)}
    result = _run(make_analysis, ["ms1"], steps)
    assert result.error is not None
    assert "ms1" in result.durations


def test_records_no_duration_for_a_skipped_block(make_analysis):
    steps = {"ms1": _StubStep(can_run=False)}
    result = _run(make_analysis, ["ms1"], steps)
    assert "ms1" in result.skipped
    assert result.durations == {}


def test_summary_survives_an_empty_durations_map(make_analysis, caplog):
    # A RunResult can reach the summary with nothing recorded — a run that failed
    # during loading, or a caller that assembled the result itself.
    from enpkg.monolith.pipeline.runner import _log_analysis_summary

    result = RunResult(analysis=make_analysis())
    with caplog.at_level(logging.INFO):
        _log_analysis_summary(logging.getLogger("test.summary.empty"), result)
    assert "TIMING" not in caplog.text
