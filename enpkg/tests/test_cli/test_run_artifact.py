"""Tests for the JSON result artifact.

The artifact is how a run's outcome crosses a process boundary: a caller that starts the
pipeline as a separate process cannot receive the ``Analysis`` back, so everything it is
allowed to display has to be in this file. These tests pin the shape and the rules a
reader depends on.
"""

import json

import pytest

from enpkg.monolith.pipeline.batch_runner import BatchResult
from enpkg.monolith.pipeline.run_artifact import (
    SCHEMA_VERSION,
    build_batch_artifact,
    build_run_artifact,
    read_artifact,
    run_result_to_dict,
    write_artifact,
)
from enpkg.monolith.pipeline.runner import STAGE_LOAD, AnalysisSummary, RunResult

_SUMMARY = AnalysisSummary(
    run_name="RUNX", n_spectra=12, n_ott_matches=3, n_network_nodes=7
)


def _ok_result(**kwargs):
    return RunResult(
        executed=["network", "ms1"],
        skipped=["weights"],
        durations={STAGE_LOAD: 1.5, "network": 40.0},
        summary=_SUMMARY,
        **kwargs,
    )


def test_run_artifact_is_json_serialisable(tmp_path):
    """Every value must survive json.dumps -- Paths and dataclasses included."""
    result = _ok_result(log_file=tmp_path / "run.log", ttl_file=tmp_path / "run.ttl")
    json.dumps(build_run_artifact(result))


def test_run_artifact_reports_paths_as_strings(tmp_path):
    result = _ok_result(log_file=tmp_path / "run.log", ttl_file=tmp_path / "run.ttl")
    artifact = build_run_artifact(result)
    assert artifact["log_file"] == str(tmp_path / "run.log")
    assert artifact["ttl_file"] == str(tmp_path / "run.ttl")
    assert artifact["summary_file"] is None


def test_absent_ttl_is_none_not_a_guess():
    """A reader must be able to tell "no graph written" from "graph at this path"."""
    assert build_run_artifact(_ok_result())["ttl_file"] is None


def test_status_follows_error():
    assert build_run_artifact(_ok_result())["status"] == "ok"
    assert build_run_artifact(RunResult(error="boom"))["status"] == "error"


def test_summary_is_flattened():
    artifact = build_run_artifact(_ok_result())
    assert artifact["summary"] == {
        "run_name": "RUNX",
        "n_spectra": 12,
        "n_ott_matches": 3,
        "n_network_nodes": 7,
    }


def test_summary_is_derived_when_only_the_analysis_is_held(monkeypatch):
    """A single run keeps its Analysis; a batch experiment keeps only the digest.

    Both must produce the same keys, so the projection derives the digest when the
    result carries an Analysis but no precomputed summary.
    """

    class FakeAnalysis:
        run_name = "DERIVED"
        spectra = [object(), object()]
        ott_matches = []
        molecular_network = None

    result = RunResult(analysis=FakeAnalysis())
    artifact = build_run_artifact(result)
    assert artifact["summary"]["run_name"] == "DERIVED"
    assert artifact["summary"]["n_spectra"] == 2


def test_durations_keep_their_stage_keys():
    """The stage sentinels are part of the contract, not decoration."""
    assert STAGE_LOAD in build_run_artifact(_ok_result())["durations"]


def test_batch_status_is_error_when_any_experiment_failed():
    batch = BatchResult(results=[_ok_result(), RunResult(error="nope")])
    artifact = build_batch_artifact(batch)
    assert artifact["status"] == "error"
    assert artifact["n_total"] == 2
    assert artifact["n_succeeded"] == 1
    assert artifact["n_failed"] == 1


def test_batch_status_is_ok_when_every_experiment_succeeded():
    batch = BatchResult(results=[_ok_result(), _ok_result()])
    assert build_batch_artifact(batch)["status"] == "ok"


def test_batch_experiments_use_the_same_projection():
    batch = BatchResult(results=[_ok_result()])
    artifact = build_batch_artifact(batch)
    assert artifact["experiments"][0] == run_result_to_dict(batch.results[0])


def test_round_trip_through_disk(tmp_path):
    path = tmp_path / "nested" / "result.json"
    artifact = build_run_artifact(_ok_result())
    write_artifact(artifact, path)
    assert read_artifact(path) == artifact


def test_read_rejects_an_unknown_schema_version(tmp_path):
    """A reader must refuse a file it cannot interpret, not read absent keys as data."""
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        read_artifact(path)


def test_read_rejects_a_non_object(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        read_artifact(path)
