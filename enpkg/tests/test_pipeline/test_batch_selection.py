"""Tests for restricting a batch to named experiments.

``_select_experiments`` and the experiment-list file are pure, so these exercise them
directly rather than running a batch.
"""
from pathlib import Path

from enpkg.monolith.pipeline.batch_runner import (
    ExperimentInputs,
    _select_experiments,
    read_experiment_list,
    write_experiment_list,
)


def _experiments(*names: str) -> list[ExperimentInputs]:
    return [
        ExperimentInputs(
            run_name=name,
            subfolder=Path(name),
            spectra_path=Path(name) / f"{name}.mgf",
            quant_path=Path(name) / f"{name}_quant.csv",
        )
        for name in names
    ]


def test_selection_keeps_discovery_order():
    chosen, unknown = _select_experiments(_experiments("a", "b", "c"), ["c", "a"])
    assert [exp.run_name for exp in chosen] == ["a", "c"]
    assert unknown == []


def test_selection_reports_names_that_match_nothing():
    chosen, unknown = _select_experiments(_experiments("a", "b"), ["a", "typo", "typo"])
    assert [exp.run_name for exp in chosen] == ["a"]
    assert unknown == ["typo"]


def test_an_empty_selection_chooses_nothing():
    chosen, unknown = _select_experiments(_experiments("a", "b"), [])
    assert chosen == []
    assert unknown == []


def test_experiment_list_round_trips(tmp_path):
    path = tmp_path / "experiments.txt"
    write_experiment_list(path, ["exp_a", "exp_b"])
    assert read_experiment_list(path) == ["exp_a", "exp_b"]


def test_experiment_list_tolerates_a_hand_written_file(tmp_path):
    """Windows line endings, stray spaces and blank lines all read as the bare names."""
    path = tmp_path / "experiments.txt"
    path.write_bytes(b"exp_a\r\n\r\n  exp_b  \r\n\r\n")
    assert read_experiment_list(path) == ["exp_a", "exp_b"]
