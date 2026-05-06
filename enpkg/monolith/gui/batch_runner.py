"""Batch runner for processing many experiments in a single invocation.

Companion to :mod:`enpkg.monolith.gui.runner`. The batch runner scans a parent
directory structured as::

    parent/
    ├── metadata.tsv        ← shared metadata for every experiment
    ├── exp_A/
    │   ├── *.mgf           ← spectra for experiment A
    │   └── *_quant.csv     ← quantification table for experiment A
    ├── exp_B/
    │   └── ...
    └── ...

and runs the selected pipeline blocks against every experiment. Expensive
shared resources — ``DBLoader`` and every non-Sirius pipeline step — are built
**once** and reused across all experiments. Sirius is rebuilt per experiment
with a deep-copied config whose input/output paths are rewritten to point at
that experiment's spectra and a per-experiment output subdirectory.
"""
from __future__ import annotations

import copy
import logging
import pickle
import queue
import tracemalloc
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from enpkg.monolith.dev_utils import log_memory_snapshot
from enpkg.monolith.gui.runner import (
    LOG_DIR,
    RunResult,
    _run_analysis,
    build_shared_steps,
    make_loggers,
)
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.pipeline.sirius_enhancement_step import SiriusEnhancementStep

# File-extension conventions — kept aligned with the single-run sidebar scan
# in ``app.py`` so users get the same behaviour between single and batch modes.
SPECTRA_SUFFIXES = (".mgf", ".mzml", ".mzxml")
QUANT_SUFFIXES = (".csv",)
METADATA_SUFFIXES = (".tsv", ".txt", ".csv")


@dataclass
class ExperimentInputs:
    """Resolved file paths for a single experiment inside the batch parent."""

    run_name: str
    subfolder: Path
    spectra_path: Path
    quant_path: Path
    # Optional sibling spectra file picked up at discovery time when present:
    # `{spectra_path.stem}_sirius{spectra_path.suffix}`. Used as the Sirius
    # block's input when that block is selected; None means no such file
    # was found in the subfolder.
    sirius_spectra_path: Optional[Path] = None


@dataclass
class BatchResult:
    """Aggregate result of a batch run.

    Holds one :class:`RunResult` per experiment plus batch-level metadata.
    ``error`` is reserved for batch-wide failures (e.g. missing shared metadata
    file, DBLoader construction failure); per-experiment failures live on each
    ``RunResult.error``.
    """

    results: list[RunResult] = field(default_factory=list)
    batch_dir: Optional[Path] = None
    runtime_log: Optional[Path] = None
    summary_log: Optional[Path] = None
    metadata_path: Optional[Path] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> list[RunResult]:
        return [r for r in self.results if r.error is None]

    @property
    def failed(self) -> list[RunResult]:
        return [r for r in self.results if r.error is not None]


def discover_experiments(parent: Path) -> list[ExperimentInputs]:
    """Scan ``parent`` for experiment subfolders and resolve each one's files.

    Each subfolder must contain exactly one spectra file and one quant file;
    subfolders missing either are skipped (the batch runner logs a warning).
    A Sirius-specific spectra file (``<stem>_sirius<spectra suffix>``) is
    picked up alongside if present and becomes available as
    ``ExperimentInputs.sirius_spectra_path``; absence is fine here and only
    becomes an error if the Sirius block is later selected for the run.

    ``run_name`` is derived from the spectra filename stem — this mirrors the
    default in :meth:`AnalysisLoader.from_files` and is what the shared
    metadata file is keyed by.
    """
    experiments: list[ExperimentInputs] = []
    for sub in sorted(p for p in parent.iterdir() if p.is_dir()):
        spectra = _first_with_suffix(sub, SPECTRA_SUFFIXES)
        if spectra is None:
            continue
        # A quant file is a CSV in the subfolder. Guard against picking up a
        # metadata CSV that someone dropped into the subfolder — the real
        # shared metadata lives at the parent level.
        quant = _first_with_suffix(sub, QUANT_SUFFIXES)
        if quant is None:
            continue
        # Look for a sibling Sirius input file with the same stem + suffix
        # plus a `_sirius` infix. Strict match keeps the contract unambiguous.
        sirius_candidate = sub / f"{spectra.stem}_sirius{spectra.suffix}"
        sirius_path = sirius_candidate if sirius_candidate.is_file() else None
        experiments.append(
            ExperimentInputs(
                run_name=spectra.stem,
                subfolder=sub,
                spectra_path=spectra,
                quant_path=quant,
                sirius_spectra_path=sirius_path,
            )
        )
    return experiments


def find_shared_metadata(parent: Path) -> Optional[Path]:
    """Return the first metadata-shaped file at ``parent`` (non-recursive).

    Prefers ``.tsv`` / ``.txt`` over ``.csv`` because the canonical metadata
    format in this project is TSV; however any match wins if only one is
    present.
    """
    for suffix in METADATA_SUFFIXES:
        for p in sorted(parent.iterdir()):
            if p.is_file() and p.suffix.lower() == suffix:
                return p
    return None


def _first_with_suffix(folder: Path, suffixes: tuple[str, ...]) -> Optional[Path]:
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in suffixes:
            return p
    return None


def _make_batch_paths() -> tuple[Path, Path, Path]:
    """Create the batch log folder and return ``(batch_dir, runtime_log, summary_log)``."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = LOG_DIR / f"batch_{stamp}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    return batch_dir, batch_dir / "runtime.log", batch_dir / "batch_summary.log"


def _sirius_config_for(run_name: str, sirius_spectra_path: Path, shared_cfg: Any) -> Any:
    """Return a deep-copied Sirius config with per-experiment paths rewritten.

    ``sirius_spectra_path`` is the experiment's Sirius-specific input file
    (the ``<stem>_sirius<suffix>`` sibling of the regular spectra file).

    The shared config's ``output_directory`` is treated as the **base** path;
    each experiment gets its own subdirectory named after ``run_name``, so
    outputs don't collide when the batch runs multiple analyses.
    """
    cfg = copy.deepcopy(shared_cfg)
    cfg.sirius_params.path_to_input_spectra = str(sirius_spectra_path)
    cfg.sirius_params.output_directory = str(
        Path(shared_cfg.sirius_params.output_directory) / run_name
    )
    return cfg


def run_batch(
    parent_dir: Path,
    selected_ids: list[str],
    configs: dict[str, Any],
    ionization_mode: str,
    database_dir: Path,
    log_queue: "queue.Queue[str]",
    verbose: bool = False,
) -> BatchResult:
    """Run the selected pipeline blocks against every experiment in ``parent_dir``.

    Shared steps (and the ``DBLoader``) are built once up front. For each
    experiment a dedicated runtime + summary log pair is written under the
    batch folder, and — if Sirius is selected — a per-experiment Sirius step
    with rewritten input/output paths is built just before execution.

    Batch-level failures (no metadata file, no experiments discovered, shared
    step construction failure) set ``BatchResult.error`` and return early.
    Per-experiment failures set the corresponding ``RunResult.error`` and
    the batch continues with the next experiment.
    """
    batch_dir, runtime_log_path, summary_log_path = _make_batch_paths()
    batch = BatchResult(
        batch_dir=batch_dir,
        runtime_log=runtime_log_path,
        summary_log=summary_log_path,
    )

    # --- Validate inputs --------------------------------------------------
    metadata_path = find_shared_metadata(parent_dir)
    if metadata_path is None:
        batch.error = f"No shared metadata file found at {parent_dir}"
        return batch
    batch.metadata_path = metadata_path

    experiments = discover_experiments(parent_dir)
    if not experiments:
        batch.error = f"No experiment subfolders with spectra + quant found under {parent_dir}"
        return batch

    # --- Build shared resources once --------------------------------------
    # Use a minimal logger just for the shared-step build; per-experiment
    # loggers are rebuilt below with their own file handlers.
    bootstrap_logger = logging.getLogger("enpkg.gui.batch.bootstrap")
    bootstrap_logger.setLevel(logging.INFO)
    try:
        shared_steps, _ = build_shared_steps(
            selected_ids,
            configs,
            database_dir,
            bootstrap_logger,
            skip={"sirius"},  # Sirius is per-experiment (paths differ).
        )
    except Exception as exc:
        batch.error = f"Shared step construction failed: {exc}"
        return batch

    sirius_shared_cfg = configs.get("sirius") if "sirius" in selected_ids else None

    # Start tracing memory allocations for the batch
    tracemalloc.start()

    # --- Iterate experiments ---------------------------------------------
    for exp in experiments:
        exp_dir = batch_dir / exp.run_name
        exp_dir.mkdir(parents=True, exist_ok=True)
        exp_log = exp_dir / "run.log"
        exp_summary = exp_dir / "summary.log"

        logger, summary_logger = make_loggers(
            log_queue, verbose=verbose, log_file=exp_log, summary_file=exp_summary
        )
        result = RunResult(log_file=exp_log, summary_file=exp_summary)
        batch.results.append(result)

        logger.info("=== [%s] Starting ===", exp.run_name)
        log_memory_snapshot(logger, f"{exp.run_name}_START")
        try:
            analysis = AnalysisLoader.from_files(
                path_to_spectra=str(exp.spectra_path),
                path_to_metadata=str(metadata_path),
                path_to_quant_table=str(exp.quant_path),
                ionization_mode=ionization_mode,
            )
            logger.info("[%s] Loaded %d spectra", exp.run_name, len(analysis.spectra))
        except Exception as exc:
            logger.exception("[%s] Failed to load analysis", exp.run_name)
            result.error = f"Analysis loading failed: {exc}"
            continue

        steps = dict(shared_steps)
        if sirius_shared_cfg is not None:
            # Sirius requires its own dedicated input file (`<stem>_sirius<suffix>`)
            # in the experiment subfolder; surface a clear error when it's
            # missing rather than silently feeding it the regular spectra file.
            if exp.sirius_spectra_path is None:
                msg = (
                    f"Sirius block is selected but no '_sirius' spectra file was found "
                    f"for experiment '{exp.run_name}' in {exp.subfolder}. Expected: "
                    f"{exp.spectra_path.stem}_sirius{exp.spectra_path.suffix}."
                )
                logger.error("[%s] %s", exp.run_name, msg)
                result.error = msg
                continue
            try:
                sirius_cfg = _sirius_config_for(
                    exp.run_name, exp.sirius_spectra_path, sirius_shared_cfg,
                )
                steps["sirius"] = SiriusEnhancementStep(config=sirius_cfg, logger=logger)
                if not Path(sirius_cfg.sirius_params.output_directory).is_dir():
                    Path(sirius_cfg.sirius_params.output_directory).mkdir(parents=True, exist_ok=True)
                logger.info("[%s] Built Sirius step with input %s and output dir %s",
                    exp.run_name,
                    sirius_cfg.sirius_params.path_to_input_spectra,
                    sirius_cfg.sirius_params.output_directory,
                )
            except Exception as exc:
                logger.exception("[%s] Failed to build Sirius step", exp.run_name)
                result.error = f"Sirius step construction failed: {exc}"
                continue

        _run_analysis(analysis, selected_ids, steps, logger, summary_logger, result)

        # Persist the per-experiment Analysis object so it can be reloaded
        # later for inspection / downstream tooling without re-running the
        # whole pipeline. Best-effort: a pickle failure is logged but does not
        # mark the experiment as failed.
        if result.analysis is not None:
            analysis_pkl = exp_dir / "analysis.pkl"
            try:
                with open(analysis_pkl, "wb") as f:
                    pickle.dump(result.analysis, f, protocol=pickle.HIGHEST_PROTOCOL)
                logger.info("[%s] Wrote analysis pickle: %s", exp.run_name, analysis_pkl)
            except Exception:
                logger.exception("[%s] Failed to write analysis pickle to %s", exp.run_name, analysis_pkl)

        log_memory_snapshot(logger, f"{exp.run_name}_END")
        logger.info("=== [%s] Finished ===", exp.run_name)

    _write_batch_summary(batch)
    return batch


def _write_batch_summary(batch: BatchResult) -> None:
    """Write the aggregate ``batch_summary.log`` describing each experiment's outcome."""
    if batch.summary_log is None:
        return
    lines: list[str] = []
    sep = "=" * 72
    lines.append(sep)
    lines.append("  BATCH RUN SUMMARY")
    lines.append(sep)
    lines.append(f"  Total experiments : {len(batch.results)}")
    lines.append(f"  Succeeded         : {len(batch.succeeded)}")
    lines.append(f"  Failed            : {len(batch.failed)}")
    if batch.metadata_path:
        lines.append(f"  Shared metadata   : {batch.metadata_path}")
    lines.append("-" * 72)
    for r in batch.results:
        name = r.analysis.run_name if r.analysis is not None else (
            r.log_file.parent.name if r.log_file else "?"
        )
        status = "OK" if r.error is None else f"FAIL: {r.error}"
        lines.append(f"  {name:<40s} {status}")
    lines.append(sep)
    batch.summary_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
