"""A machine-readable record of what a run did, written alongside its logs.

A caller that starts a run as a separate operating-system process cannot receive the
``Analysis`` back: that object lives in the other process's memory and is gone when it
exits. What it can receive is a small JSON file. This module owns the shape of that
file, so a writer and a reader cannot disagree about it.

The contents are a projection of :class:`RunResult` and :class:`BatchResult`: every
field except the ``Analysis`` itself, which is represented by its
:class:`AnalysisSummary` digest. ``schema_version`` lets a reader reject a file it does
not understand instead of reading absent keys as absent data.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _path_str(value: Path | None) -> str | None:
    return str(value) if value is not None else None


def run_result_to_dict(result: Any) -> dict[str, Any]:
    """Project one :class:`RunResult` onto JSON-safe primitives.

    ``summary`` is taken from the result when the batch runner has already computed it,
    and derived from a still-held ``Analysis`` otherwise, so a single run and a batch
    experiment produce the same keys.
    """
    summary = result.summary
    if summary is None and result.analysis is not None:
        from enpkg.monolith.pipeline.runner import AnalysisSummary

        summary = AnalysisSummary.from_analysis(result.analysis)
    return {
        "status": "error" if result.error else "ok",
        "error": result.error,
        "executed": list(result.executed),
        "skipped": list(result.skipped),
        "durations": dict(result.durations),
        "summary": asdict(summary) if summary is not None else None,
        "log_file": _path_str(result.log_file),
        "summary_file": _path_str(result.summary_file),
        "ttl_file": _path_str(result.ttl_file),
    }


def build_run_artifact(result: Any) -> dict[str, Any]:
    """Return the artifact describing a single-experiment run."""
    return {"schema_version": SCHEMA_VERSION, "kind": "run", **run_result_to_dict(result)}


def build_batch_artifact(batch: Any) -> dict[str, Any]:
    """Return the artifact describing a batch run.

    The top-level ``status`` covers the batch as a whole: it reads ``error`` when any
    experiment failed, so a caller inspecting only the top level cannot miss a
    per-experiment failure. Each entry of ``experiments`` carries its own status.
    """
    experiments = [run_result_to_dict(r) for r in batch.results]
    failed = [e for e in experiments if e["status"] == "error"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "batch",
        "status": "error" if (batch.error or failed) else "ok",
        "error": batch.error,
        "batch_dir": _path_str(batch.batch_dir),
        "summary_log": _path_str(batch.summary_log),
        "metadata_path": _path_str(batch.metadata_path),
        "n_total": len(experiments),
        "n_succeeded": len(experiments) - len(failed),
        "n_failed": len(failed),
        "experiments": experiments,
    }


def write_artifact(artifact: dict[str, Any], path: Path) -> None:
    """Write an artifact to ``path``, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def read_artifact(path: Path) -> dict[str, Any]:
    """Read an artifact, rejecting a schema version this code does not understand.

    Raises:
        ValueError: the file does not hold a JSON object, or carries a different
            schema version.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{path} has schema_version {version!r}; this build understands "
            f"{SCHEMA_VERSION}"
        )
    return data
