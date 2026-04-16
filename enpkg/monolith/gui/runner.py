"""Pipeline runner for the GUI.

Mirrors the orchestration in ``enpkg/monolith/pipeline/test.py`` but is driven
by the set of blocks selected in the GUI. A ``logging.Handler`` pushes log
records onto a queue that the Streamlit app can drain into the UI.
"""
from __future__ import annotations

import logging
import queue
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.gui.blocks import BLOCKS, BLOCKS_BY_ID
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.database_loader import DBLoader


@dataclass
class RunResult:
    analysis: Optional[Analysis] = None
    executed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: Optional[str] = None


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


def make_logger(q: "queue.Queue[str]", verbose: bool) -> logging.Logger:
    logger = logging.getLogger("enpkg.gui.runner")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.addHandler(QueueLogHandler(q))

    # Add StreamHandler for stdio output
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(stream_handler)

    logger.propagate = False
    return logger


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
    logger = make_logger(log_queue, verbose=verbose)
    result = RunResult()

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
        logger.debug("Analysis object created with %d compounds", len(analysis.scores) if hasattr(analysis, 'scores') else 0)
    except Exception as exc:
        logger.exception("Failed to load analysis")
        result.error = f"Analysis loading failed: {exc}"
        return result

    selected_set = set(selected_ids)
    logger.debug("Selected blocks set: %s", selected_set)

    # Shared DBLoader used by both MS1 and MS2 if either is selected.
    db_loader: Optional[DBLoader] = None
    ms_config: Optional[MSEnhancerConfig] = None
    if selected_set & {"ms1", "ms2"}:
        logger.debug("MS1 or MS2 selected, initializing MS config and DBLoader")
        ms_config = configs.get("ms1") or configs.get("ms2")
        if ms_config is not None:
            logger.debug("MS config found, applying download directory")
            _apply_download_dir(ms_config, database_dir)
            logger.debug("Creating DBLoader with MS config")
            db_loader = DBLoader(configuration=ms_config, logger=logger)
            logger.debug("DBLoader initialized successfully")
        else:
            logger.debug("No MS config found for ms1/ms2")
    else:
        logger.debug("MS1 and MS2 not selected")

    # Weights also needs a DBLoader (see weights_enhancement_step).
    weights_config = configs.get("weights")
    if weights_config is not None and db_loader is None:
        logger.debug("Weights selected without existing DBLoader, creating new one")
        # Reuse MS config if present, else fall back to a minimal one.
        if ms_config is None:
            logger.debug("No MS config, creating minimal MSEnhancerConfig for weights")
            ms_config = MSEnhancerConfig()
            _apply_download_dir(ms_config, database_dir)
        db_loader = DBLoader(configuration=ms_config, logger=logger)
        logger.debug("DBLoader created for weights")
    elif weights_config is None:
        logger.debug("Weights not selected")

    for block in BLOCKS:
        logger.debug("Processing block: %s (%s)", block.id, block.label)
        if block.id not in selected_set:
            logger.debug("Block %s not selected, skipping", block.id)
            continue

        missing_deps = [dep for dep in block.depends_on if dep not in selected_set]
        if missing_deps:
            logger.warning("Skipping %s: missing dependencies %s", block.id, missing_deps)
            result.skipped.append(block.id)
            continue
        else:
            logger.debug("Block %s has all dependencies", block.id)

        try:
            logger.debug("Building step for %s with config: %s", block.id, type(configs.get(block.id)).__name__)
            step = _build_step(block.id, configs.get(block.id), logger, db_loader)
            logger.debug("Step %s built successfully", block.id)
        except Exception as exc:
            logger.exception("Failed to construct step %s", block.id)
            result.error = f"{block.id}: {exc}"
            return result

        logger.debug("Checking if %s can run with current analysis state", block.id)
        if not step.can_run(analysis):
            logger.info("Skipping %s: can_run() returned False", block.id)
            result.skipped.append(block.id)
            continue

        logger.info("Running %s …", block.label)
        logger.debug("Executing process step for %s", block.id)
        try:
            analysis = step.process(analysis)
            logger.debug("Process step for %s completed, analysis state updated", block.id)
        except Exception as exc:
            logger.exception("Step %s failed", block.id)
            result.error = f"{block.id}: {exc}"
            result.analysis = analysis
            return result
        result.executed.append(block.id)
        logger.info("%s completed", block.label)
        logger.debug("Block %s marked as executed", block.id)
    
    

    logger.info("Pipeline execution completed successfully")
    logger.debug("Executed blocks: %s, Skipped blocks: %s", result.executed, result.skipped)
    result.analysis = analysis
    return result


def _apply_download_dir(ms_config: MSEnhancerConfig, database_dir: Path) -> None:
    database_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("enpkg.gui.runner")
    logger.debug("Setting download directory to: %s", database_dir)
    ms_config.downloader_params.download_dir = str(database_dir)


def _build_step(block_id: str, config: Any, logger: logging.Logger, db_loader: Optional[DBLoader]):
    block = BLOCKS_BY_ID[block_id]
    cls = block.step_cls
    logger.debug("Building step for block_id=%s, step_class=%s", block_id, cls.__name__)

    if block_id == "taxonomical":
        logger.debug("Instantiating Taxonomical step with no parameters")
        return cls()
    if block_id == "network":
        logger.debug("Instantiating Network step with config")
        return cls(config)
    if block_id in ("ms1", "ms2"):
        logger.debug("Instantiating %s step with config and db_loader", block_id.upper())
        return cls(config=config, logger=logger, db_loader=db_loader)
    if block_id == "sirius":
        logger.debug("Instantiating Sirius step with config and logger")
        return cls(config=config, logger=logger)
    if block_id == "weights":
        logger.debug("Instantiating Weights step with config, logger, and db_loader")
        return cls(config=config, logger=logger, db_loader=db_loader)
    raise ValueError(f"Unknown block id: {block_id}")
