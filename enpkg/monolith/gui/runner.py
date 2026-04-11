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
    def __init__(self, q: "queue.Queue[str]") -> None:
        super().__init__()
        self.queue = q
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None: 
        try:
            self.queue.put_nowait(self.format(record))
        except queue.Full:
            pass


def make_logger(q: "queue.Queue[str]", verbose: bool) -> logging.Logger:
    logger = logging.getLogger("enpkg.gui.runner")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.addHandler(QueueLogHandler(q))
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

    try:
        logger.info("Loading analysis from %s", spectra_path)
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

    selected_set = set(selected_ids)

    # Shared DBLoader used by both MS1 and MS2 if either is selected.
    db_loader: Optional[DBLoader] = None
    ms_config: Optional[MSEnhancerConfig] = None
    if selected_set & {"ms1", "ms2"}:
        ms_config = configs.get("ms1") or configs.get("ms2")
        if ms_config is not None:
            _apply_download_dir(ms_config, database_dir)
            db_loader = DBLoader(configuration=ms_config, logger=logger)

    # Weights also needs a DBLoader (see weights_enhancement_step).
    weights_config = configs.get("weights")
    if weights_config is not None and db_loader is None:
        # Reuse MS config if present, else fall back to a minimal one.
        if ms_config is None:
            ms_config = MSEnhancerConfig()
            _apply_download_dir(ms_config, database_dir)
        db_loader = DBLoader(configuration=ms_config, logger=logger)

    for block in BLOCKS:
        if block.id not in selected_set:
            continue
        if not all(dep in selected_set for dep in block.depends_on):
            logger.warning("Skipping %s: missing dependencies %s", block.id, block.depends_on)
            result.skipped.append(block.id)
            continue

        try:
            step = _build_step(block.id, configs.get(block.id), logger, db_loader)
        except Exception as exc:
            logger.exception("Failed to construct step %s", block.id)
            result.error = f"{block.id}: {exc}"
            return result

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
            return result
        result.executed.append(block.id)
        logger.info("%s completed", block.label)

    result.analysis = analysis
    return result


def _apply_download_dir(ms_config: MSEnhancerConfig, database_dir: Path) -> None:
    database_dir.mkdir(parents=True, exist_ok=True)
    ms_config.downloader_params.download_dir = str(database_dir)


def _build_step(block_id: str, config: Any, logger: logging.Logger, db_loader: Optional[DBLoader]):
    block = BLOCKS_BY_ID[block_id]
    cls = block.step_cls
    if block_id == "taxonomical":
        return cls()
    if block_id == "network":
        return cls(config)
    if block_id in ("ms1", "ms2"):
        return cls(config=config, logger=logger, db_loader=db_loader)
    if block_id == "sirius":
        return cls(config=config, logger=logger)
    if block_id == "weights":
        return cls(config=config, logger=logger, db_loader=db_loader)
    raise ValueError(f"Unknown block id: {block_id}")
