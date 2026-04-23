"""Pipeline runner for the GUI.

Mirrors the orchestration in ``enpkg/monolith/pipeline/test.py`` but is driven
by the set of blocks selected in the GUI. A ``logging.Handler`` pushes log
records onto a queue that the Streamlit app can drain into the UI.
"""
from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.exceptions import DBLoaderError
from enpkg.monolith.gui.blocks import BLOCKS, BLOCKS_BY_ID
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.loaders.lotus_store import LotusStore

LOG_DIR = Path("gui_workspace") / "logs"


@dataclass
class RunResult:
    analysis: Optional[Analysis] = None
    executed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: Optional[str] = None
    log_file: Optional[Path] = None
    summary_file: Optional[Path] = None


class QueueLogHandler(logging.Handler):
    """A logging handler that writes formatted log records to a thread-safe queue.

    Used to bridge Python's logging system with the Streamlit UI: the pipeline
    runs in the main thread and pushes log lines onto the queue; the app drains
    the queue after the run and renders the lines in a code block.
    """

    def __init__(self, q: "queue.Queue[str]") -> None:
        """Attach the handler to ``q`` and set the default log format.

        Args:
            q: Queue that receives formatted log strings. Should be created
               with ``queue.Queue()`` (unbounded) so that ``put_nowait`` never
               raises ``Full`` under normal usage.
        """
        super().__init__()
        self.queue = q
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        """Format ``record`` and push the resulting string onto the queue.

        Silently drops the record if the queue is full rather than blocking or
        raising, so a slow UI cannot stall the pipeline.

        Args:
            record: The log record emitted by the logger.
        """
        try:
            self.queue.put_nowait(self.format(record))
        except queue.Full:
            pass


def _make_log_paths() -> tuple[Path, Path]:
    """Create matching timestamped paths for the runtime and summary log files.

    Both files share the same ``YYYYMMDD_HHMMSS`` stamp so they can be paired
    visually in the logs directory.

    Returns:
        (runtime_log_path, summary_log_path) — ``run_*.log`` and ``summary_*.log``
        under ``gui_workspace/logs/``.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"run_{stamp}.log", LOG_DIR / f"summary_{stamp}.log"


def make_loggers(
    q: "queue.Queue[str]",
    verbose: bool,
    log_file: Path,
    summary_file: Path,
) -> tuple[logging.Logger, logging.Logger]:
    """Build the runtime and summary loggers in one call.

    The runtime logger (``enpkg.gui.runner``) captures everything the pipeline
    emits during execution and writes it to ``log_file``. The summary logger
    (``enpkg.gui.summary``) is used exclusively by ``_log_analysis_summary``
    to write the pretty post-run report to ``summary_file``.

    Both loggers share the same ``QueueLogHandler`` queue so the Streamlit UI
    expander still shows runtime lines and the summary together. Only the
    runtime logger adds a console stream handler — the summary doesn't need
    to be duplicated to the server log.

    Args:
        q: Shared queue feeding the Streamlit UI.
        verbose: When True, runtime queue/stream handlers emit DEBUG records.
        log_file: Destination path for the runtime log.
        summary_file: Destination path for the summary log.

    Returns:
        ``(runtime_logger, summary_logger)``.
    """
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    level = logging.DEBUG if verbose else logging.INFO

    # --- Runtime logger ---------------------------------------------------
    runtime_logger = logging.getLogger("enpkg.gui.runner")
    runtime_logger.setLevel(logging.DEBUG)  # capture everything; handlers filter
    runtime_logger.handlers.clear()

    queue_handler = QueueLogHandler(q)
    queue_handler.setLevel(level)
    runtime_logger.addHandler(queue_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(fmt)
    runtime_logger.addHandler(stream_handler)

    runtime_file_handler = logging.FileHandler(str(log_file), mode="w", encoding="utf-8")
    runtime_file_handler.setLevel(logging.DEBUG)  # always write everything to disk
    runtime_file_handler.setFormatter(fmt)
    runtime_logger.addHandler(runtime_file_handler)

    runtime_logger.propagate = False

    # --- Summary logger ---------------------------------------------------
    summary_logger = logging.getLogger("enpkg.gui.summary")
    summary_logger.setLevel(logging.INFO)
    summary_logger.handlers.clear()

    summary_queue_handler = QueueLogHandler(q)
    summary_queue_handler.setLevel(logging.INFO)
    summary_logger.addHandler(summary_queue_handler)

    summary_file_handler = logging.FileHandler(str(summary_file), mode="w", encoding="utf-8")
    summary_file_handler.setLevel(logging.INFO)
    summary_file_handler.setFormatter(fmt)
    summary_logger.addHandler(summary_file_handler)

    summary_logger.propagate = False

    return runtime_logger, summary_logger


def run_pipeline(
    selected_ids: list[str],
    configs: dict[str, Any],
    spectra_path: Path,
    metadata_path: Path,
    quant_path: Path,
    ionization_mode: str,
    database_dir: Path,
    log_queue: "queue.Queue[str]",
    verbose: bool = False
) -> RunResult:
    """Load an Analysis and run each selected block in canonical order."""
    log_file, summary_file = _make_log_paths()
    logger, summary_logger = make_loggers(
        log_queue, verbose=verbose, log_file=log_file, summary_file=summary_file
    )
    result = RunResult(log_file=log_file, summary_file=summary_file)

    logger.debug("Starting pipeline run with selected blocks: %s", selected_ids)
    logger.debug("Ionization mode: %s, database_dir: %s", ionization_mode, database_dir)

    try:
        logger.info("Loading analysis from %s", spectra_path)
        logger.debug("Metadata path: %s, Quant path: %s", metadata_path, quant_path)
        analysis = AnalysisLoader.from_files(
            path_to_spectra=str(spectra_path),
            path_to_metadata=str(metadata_path),
            path_to_quant_table=str(quant_path),
            ionization_mode=ionization_mode,
        )
        logger.info("Loaded %d spectra", len(analysis.spectra))
    except Exception as exc:
        logger.exception("Failed to load analysis")
        result.error = f"Analysis loading failed: {exc}"
        return result

    try:
        steps, _ = build_shared_steps(selected_ids, configs, database_dir, logger)
    except Exception as exc:
        logger.exception("Failed to construct pipeline steps")
        result.error = f"Step construction failed: {exc}"
        return result

    return _run_analysis(analysis, selected_ids, steps, logger, summary_logger, result)


def build_shared_steps(
    selected_ids: list[str],
    configs: dict[str, Any],
    database_dir: Path,
    logger: logging.Logger,
    skip: Optional[set[str]] = None,
) -> tuple[dict[str, Any], Optional[DBLoader]]:
    """Build step instances for every selected block plus the shared ``DBLoader``.

    Factored out of ``run_pipeline`` so the batch runner can construct shared
    resources once and reuse them across many analyses. Steps listed in ``skip``
    are omitted from the returned map — the batch runner uses this to build
    Sirius per-analysis (its config carries per-experiment paths).

    Args:
        selected_ids: Block ids the caller wants to run.
        configs: Validated config objects keyed by block id.
        database_dir: Target directory for DB downloads.
        logger: Runtime logger shared with the built steps.
        skip: Optional set of block ids to exclude from the returned step map.

    Returns:
        ``(steps, db_loader)`` — a map from block id to step instance, and the
        shared ``DBLoader`` (or ``None`` if neither MS1/MS2 nor weights was
        selected).
    """
    skip = skip or set()
    selected_set = set(selected_ids)

    # Shared DBLoader covers MS1, MS2, and weights — only build it once.
    db_loader: Optional[DBLoader] = None
    lotus_store: Optional[LotusStore] = None
    ms_config: Optional[MSEnhancerConfig] = None
    if selected_set & {"ms1", "ms2"}:
        ms_config = configs.get("ms1") or configs.get("ms2")
        if ms_config is not None:
            _apply_download_dir(ms_config, database_dir)
            db_loader = DBLoader(configuration=ms_config, logger=logger)
            logger.debug("DBLoader initialized for MS1/MS2")

    if "weights" in selected_set and db_loader is None:
        if ms_config is None:
            ms_config = MSEnhancerConfig()
            _apply_download_dir(ms_config, database_dir)
        db_loader = DBLoader(configuration=ms_config, logger=logger)
        logger.debug("DBLoader initialized for weights")

    # LotusStore is built from the same DuckDB file DBLoader was pointed at.
    # It owns all compound-side Lotus access (shared by MS1, MS2, weights).
    if db_loader is not None and (selected_set & {"ms1", "ms2", "weights"}):
        duckdb_path = ms_config.downloader_params.duckdb_path if ms_config else None
        if not duckdb_path:
            raise DBLoaderError(
                "A DuckDB path is required for MS1/MS2/weights; CSV fallback is no longer supported."
            )
        lotus_store = LotusStore(duckdb_path=duckdb_path, logger=logger)
        logger.debug("LotusStore initialized from %s", duckdb_path)

    steps: dict[str, Any] = {}
    for block_id in selected_ids:
        if block_id in skip:
            continue
        steps[block_id] = _build_step(
            block_id, configs.get(block_id), logger, db_loader, lotus_store,
        )
        logger.debug("Built step %s", block_id)

    return steps, db_loader


def _run_analysis(
    analysis: Analysis,
    selected_ids: list[str],
    steps: dict[str, Any],
    logger: logging.Logger,
    summary_logger: logging.Logger,
    result: RunResult,
) -> RunResult:
    """Execute the selected blocks against ``analysis`` using pre-built steps.

    Shared execution core used by both the single-run ``run_pipeline`` and the
    batch runner. Mutates and returns ``result``; writes the pretty summary via
    ``summary_logger`` and closes its file handler.

    Missing step instances in ``steps`` for a selected block cause that block
    to be skipped (the batch runner may intentionally omit a step; the key
    error otherwise would already have surfaced during ``build_shared_steps``).
    """
    selected_set = set(selected_ids)

    for block in BLOCKS:
        if block.id not in selected_set:
            continue

        missing_deps = [dep for dep in block.depends_on if dep not in selected_set]
        if missing_deps:
            logger.warning("Skipping %s: missing dependencies %s", block.id, missing_deps)
            result.skipped.append(block.id)
            continue

        step = steps.get(block.id)
        if step is None:
            logger.warning("Skipping %s: no step instance provided", block.id)
            result.skipped.append(block.id)
            continue

        if not step.can_run(analysis):
            logger.info("Skipping %s: can_run() returned False", block.id)
            result.skipped.append(block.id)
            continue

        logger.info("Running %s …", block.label)
        try:
            analysis = step.process(analysis)
        except Exception as exc:
            logger.exception("Step %s failed", block.id)
            result.error = f"{block.id}: {exc}"
            result.analysis = analysis
            _close_file_handlers(summary_logger)
            return result
        result.executed.append(block.id)
        logger.info("%s completed", block.label)

    result.analysis = analysis
    logger.info("Pipeline execution completed successfully")
    _log_analysis_summary(summary_logger, result)
    _close_file_handlers(summary_logger)
    return result


def _close_file_handlers(logger: logging.Logger) -> None:
    """Flush and close all file handlers on ``logger``.

    Streamlit sessions are long-lived, so we can't rely on interpreter-exit
    shutdown to flush per-run summary files to disk.
    """
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            h.close()


def _log_analysis_summary(logger: logging.Logger, result: RunResult) -> None:
    """Log a pretty summary of the pipeline result and final Analysis state.

    The summary has two parts:

    1. A fixed header with general metadata (run name, sample, spectra count).
    2. One section per *executed* block, produced by calling the block's
       ``log_summary`` function from the registry.  Blocks that were skipped
       or not selected do not get a section — only blocks that actually ran
       contribute to the report.
    """
    sep = "=" * 72
    logger.info(sep)
    logger.info("  PIPELINE RUN SUMMARY")
    logger.info(sep)

    logger.info("  Executed blocks : %s", ", ".join(result.executed) or "(none)")
    logger.info("  Skipped blocks  : %s", ", ".join(result.skipped) or "(none)")
    if result.error:
        logger.info("  Error           : %s", result.error)

    analysis = result.analysis
    if analysis is None:
        logger.info("  (no analysis object available)")
        logger.info(sep)
        return

    # -- Fixed header: general analysis metadata --
    logger.info("-" * 72)
    logger.info("  ANALYSIS: %s", analysis.run_name)
    logger.info("-" * 72)
    meta = analysis.metadata
    logger.info("  Sample ID       : %s", meta.sample_id)
    logger.info("  Source taxon    : %s", meta.source_taxon or "(not set)")
    logger.info("  Ionization mode : %s", analysis.ionization_mode)
    logger.info("  Total spectra   : %d", len(analysis.spectra))

    # -- Per-block sections: only for blocks that actually executed --
    for block_id in result.executed:
        block = BLOCKS_BY_ID[block_id]
        logger.info("-" * 72)
        logger.info("  %s", block.label.upper())
        block.log_summary(logger, analysis)

    logger.info(sep)
    if result.log_file:
        logger.info("  Full log written to: %s", result.log_file)
    logger.info(sep)


def _apply_download_dir(ms_config: MSEnhancerConfig, database_dir: Path) -> None:
    database_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("enpkg.gui.runner")
    logger.debug("Setting download directory to: %s", database_dir)
    ms_config.downloader_params.download_dir = str(database_dir)


def _build_step(
    block_id: str,
    config: Any,
    logger: logging.Logger,
    db_loader: Optional[DBLoader],
    lotus_store: Optional[LotusStore],
) -> Any:
    block = BLOCKS_BY_ID[block_id]
    cls = block.step_cls
    logger.debug("Building step for block_id=%s, step_class=%s", block_id, cls.__name__)

    if block_id == "taxonomical":
        logger.debug("Instantiating Taxonomical step with no parameters")
        return cls()
    if block_id == "network":
        logger.debug("Instantiating Network step with config")
        return cls(config)
    if block_id == "ms1":
        # MS1 needs the LotusStore for mass-windowed Lotus access; db_loader is still
        # threaded through until Step 3 of the refactor migrates MS1 off it.
        logger.debug("Instantiating MS1 step with config, db_loader and lotus_store")
        return cls(config=config, logger=logger, db_loader=db_loader)
    if block_id == "ms2":
        logger.debug("Instantiating MS2 step with config, db_loader and lotus_store")
        return cls(
            config=config, logger=logger,
            db_loader=db_loader, lotus_store=lotus_store,
        )
    if block_id == "sirius":
        logger.debug("Instantiating Sirius step with config and logger")
        return cls(config=config, logger=logger)
    if block_id == "weights":
        logger.debug("Instantiating Weights step with config, logger, and db_loader")
        return cls(config=config, logger=logger, db_loader=db_loader)
    raise ValueError(f"Unknown block id: {block_id}")
