"""Single-analysis pipeline runner.

Drives the pipeline from a set of selected block ids: each block from the
[blocks.py](blocks.py) registry is bound to its config and the run's shared
resources, then executed in registry order via the uniform
``enhance(analysis) -> Analysis`` contract.

The module depends on no front-end. A ``logging.Handler`` pushes log records
onto a queue so a caller that wants live output — the Streamlit app does — can
drain it; a headless caller can pass a queue it never reads.
"""
from __future__ import annotations

import logging
import queue
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.exceptions import DatabaseError
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.loaders.spectral_library_store import SpectralLibraryStore
from enpkg.monolith.pipeline.blocks import (
    BLOCKS,
    BLOCKS_BY_ID,
    BlockSpec,
    BuildContext,
    required_resources,
)
from enpkg.monolith.rdf import serialize_to_turtle

LOG_DIR = Path("gui_workspace") / "logs"

# Keys under which ``RunResult.durations`` records the work that happens outside
# the block loop. The dunder form cannot collide with a block id: `BlockSpec.id`
# values are plain lowercase names ("sirius", "ms1_graph", …).
STAGE_LOAD = "__load__"
STAGE_RDF = "__rdf__"
STAGE_PICKLE = "__pickle__"

# Human-readable names for the stage keys above, used by the run and batch reports.
STAGE_LABELS: dict[str, str] = {
    STAGE_LOAD: "Load analysis",
    STAGE_RDF: "RDF serialization",
    STAGE_PICKLE: "Pickle analysis",
}


@contextmanager
def record_duration(durations: dict[str, float], key: str) -> Iterator[None]:
    """Time the enclosed block and store the elapsed seconds under ``key``.

    Records on the exception path as well as the success path: a step that runs
    for twenty minutes and then raises is exactly the one whose duration is worth
    reading, and the runner abandons the analysis as soon as a step raises.

    Uses ``time.perf_counter``, which is monotonic, so a system clock adjustment
    part-way through a long run cannot produce a negative duration.

    Args:
        durations: Mapping to write into; the key is overwritten if already present.
        key: A block id, or one of the ``STAGE_*`` constants for work outside the
            block loop.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        durations[key] = time.perf_counter() - start


@dataclass(frozen=True)
class AnalysisSummary:
    """Cheap-to-keep digest of an Analysis used by the GUI after the full
    Analysis object has been freed.

    Populated from a fully-enhanced Analysis just before ``result.analysis``
    is dropped at the end of a batch experiment so the post-batch GUI panel
    can still show counts without retaining the multi-GB Analysis tree.
    """

    run_name: str
    n_spectra: int
    n_ott_matches: int
    n_network_nodes: int

    @classmethod
    def from_analysis(cls, analysis: Analysis) -> "AnalysisSummary":
        network = getattr(analysis, "molecular_network", None)
        return cls(
            run_name=analysis.run_name,
            n_spectra=len(analysis.spectra),
            n_ott_matches=len(getattr(analysis, "ott_matches", []) or []),
            n_network_nodes=len(network.nodes) if network is not None else 0,
        )


@dataclass
class RunResult:
    analysis: Optional[Analysis] = None
    executed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: Optional[str] = None
    log_file: Optional[Path] = None
    summary_file: Optional[Path] = None
    # Wall-clock seconds per block id, plus the STAGE_* keys for the loading,
    # serialization and pickling that happen outside the block loop. A plain dict
    # of floats, so it outlives `analysis` being dropped at the end of a batch
    # experiment and stays available to the GUI.
    durations: dict[str, float] = field(default_factory=dict)
    # Populated by the batch runner once `analysis` has been pickled and
    # freed; the GUI prefers `analysis` when present, falls back here.
    summary: Optional[AnalysisSummary] = None


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


def run_stamp() -> str:
    """Return a filename-safe stamp that is unique within one process.

    The wall-clock part is one-second granular, so two runs started in the same second
    would land on the same paths and the second would overwrite the first's logs. The
    four random characters make that collision negligible without making the name
    unreadable — the stamp still sorts chronologically.
    """
    return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"


def _make_log_paths(output_dir: Path | None = None) -> tuple[Path, Path]:
    """Create matching stamped paths for the runtime and summary log files.

    Both files share one stamp so they can be paired visually in the logs directory.

    Args:
        output_dir: Directory to write into. Defaults to ``LOG_DIR``, which is relative
            to the process working directory — callers that must not depend on where
            they were launched from (the command line, and the GUI spawning it) pass an
            absolute path instead.

    Returns:
        (runtime_log_path, summary_log_path) — ``run_*.log`` and ``summary_*.log``.
    """
    base = output_dir if output_dir is not None else LOG_DIR
    base.mkdir(parents=True, exist_ok=True)
    stamp = run_stamp()
    return base / f"run_{stamp}.log", base / f"summary_{stamp}.log"


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
    log_queue: "queue.Queue[str]",
    verbose: bool = False,
    output_dir: Path | None = None,
) -> RunResult:
    """Load an Analysis and run each selected block in canonical order.

    Args:
        output_dir: Where the run log, summary log and Turtle export are written.
            Defaults to ``LOG_DIR``, which is relative to the process working directory.
    """
    log_file, summary_file = _make_log_paths(output_dir)
    logger, summary_logger = make_loggers(
        log_queue, verbose=verbose, log_file=log_file, summary_file=summary_file
    )
    result = RunResult(log_file=log_file, summary_file=summary_file)

    logger.debug("Starting pipeline run with selected blocks: %s", selected_ids)
    logger.debug("Ionization mode: %s", ionization_mode)

    try:
        logger.info("Loading analysis from %s", spectra_path)
        logger.debug("Metadata path: %s, Quant path: %s", metadata_path, quant_path)
        with record_duration(result.durations, STAGE_LOAD):
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
        steps = build_shared_steps(selected_ids, configs, logger)
    except Exception as exc:
        logger.exception("Failed to construct pipeline steps")
        result.error = f"Step construction failed: {exc}"
        return result

    result = _run_analysis(analysis, selected_ids, steps, logger, summary_logger, result)

    # Best-effort RDF/Turtle export next to the run log (mirrors the batch runner).
    if result.analysis is not None and result.error is None and result.log_file is not None:
        ttl_path = result.log_file.with_suffix(".ttl")
        try:
            with record_duration(result.durations, STAGE_RDF):
                serialize_to_turtle(
                    result.analysis, str(ttl_path),
                    include_network="network" in result.executed,
                )
            # Logged rather than shown in the summary: the summary is written and
            # its file handler closed inside `_run_analysis`, which has already
            # returned by this point.
            logger.info(
                "Wrote RDF graph: %s (%.1fs)", ttl_path, result.durations[STAGE_RDF]
            )
        except Exception:
            logger.exception("Failed to write RDF graph to %s", ttl_path)

    return result


def build_shared_steps(
    selected_ids: list[str],
    configs: dict[str, Any],
    logger: logging.Logger,
    skip: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Build step instances for every selected block.

    Factored out of ``run_pipeline`` so the batch runner can construct shared
    resources once and reuse them across many analyses. Steps listed in ``skip``
    are omitted from the returned map — the batch runner uses this to build
    Sirius per-analysis (its config carries per-experiment paths).

    Which shared resources get built is decided by the selected blocks'
    ``BlockSpec.requires``, so a block only receives a resource it asked for and
    a run only pays for what it uses.

    Args:
        selected_ids: Block ids the caller wants to run.
        configs: Validated config objects keyed by block id.
        logger: Runtime logger shared with the built steps.
        skip: Optional set of block ids to exclude from the returned step map.

    Raises:
        DatabaseError: If a selected block needs database access but no
            MSEnhancerConfig was supplied to say which database.

    Returns:
        A map from block id to step instance.
    """
    skip = skip or set()

    lotus_store: Optional[LotusStore] = None
    library_store: Optional[SpectralLibraryStore] = None

    needed = required_resources(selected_ids)
    if needed:
        # Database access is configured by the MSEnhancerConfig that MS1 and MS2
        # share. A block can require it without either being selected — `weights`
        # does — and there is no default database path to fall back to, so the
        # absence of a config is reported here rather than surfacing later as a
        # missing-file error.
        ms_config: Optional[MSEnhancerConfig] = configs.get("ms1") or configs.get("ms2")
        if ms_config is None:
            raise DatabaseError(
                f"Block(s) requiring database access ({', '.join(sorted(needed))}) "
                "were selected, but no MS enhancer configuration was supplied to "
                "name the DuckDB database. Select the ms1 or ms2 block, or provide "
                "its configuration."
            )

        if "lotus_store" in needed:
            lotus_store = LotusStore(
                duckdb_path=ms_config.duckdb_path, logger=logger
            )
            logger.debug("LotusStore initialized from %s", ms_config.duckdb_path)

        if "spectral_library_store" in needed:
            library_store = SpectralLibraryStore(
                duckdb_path=ms_config.duckdb_path,
                logger=logger,
                library_names=ms_config.spectral_libraries,
            )
            logger.debug(
                "SpectralLibraryStore initialized from %s", ms_config.duckdb_path
            )

    steps: dict[str, Any] = {}
    for block_id in selected_ids:
        if block_id in skip:
            continue
        steps[block_id] = _build_step(
            block_id, configs.get(block_id), logger, lotus_store, library_store,
        )
        logger.debug("Built step %s", block_id)

    return steps


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
            with record_duration(result.durations, block.id):
                analysis = step.process(analysis)
        except Exception as exc:
            logger.exception("Step %s failed", block.id)
            result.error = f"{block.id}: {exc}"
            result.analysis = analysis
            _close_file_handlers(summary_logger)
            return result
        result.executed.append(block.id)
        logger.info("%s completed in %.1fs", block.label, result.durations[block.id])

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
        elapsed = result.durations.get(block_id)
        logger.info("-" * 72)
        # A block with no recorded duration prints "-" rather than raising: a
        # RunResult can reach here with an empty `durations` (a run that failed
        # before the block loop, or a caller that built the result itself).
        logger.info(
            "  %s  [%s]",
            block.label.upper(),
            f"{elapsed:.1f}s" if elapsed is not None else "-",
        )
        block.log_summary(logger, analysis)

    _log_timing_section(logger, result)

    logger.info(sep)
    if result.log_file:
        logger.info("  Full log written to: %s", result.log_file)
    logger.info(sep)


def _log_timing_section(logger: logging.Logger, result: RunResult) -> None:
    """Log the per-stage timing breakdown for one analysis.

    Lists every executed block and every ``STAGE_*`` entry recorded so far,
    slowest first, with each one's share of the total. Stages that run after
    ``_log_analysis_summary`` — RDF serialization and pickling, both driven by
    the callers — are absent here by construction; the batch report collects
    them once every experiment has finished.

    Emits nothing when no durations were recorded, so a caller that built a
    ``RunResult`` without timing still gets a well-formed summary.
    """
    if not result.durations:
        return

    total = sum(result.durations.values())
    logger.info("-" * 72)
    logger.info("  TIMING  (total %.1fs)", total)
    for key, elapsed in sorted(result.durations.items(), key=lambda kv: -kv[1]):
        label = STAGE_LABELS.get(key) or (
            BLOCKS_BY_ID[key].label if key in BLOCKS_BY_ID else key
        )
        share = 100.0 * elapsed / total if total > 0 else 0.0
        logger.info("    %-28s %9.1fs  %5.1f%%", label, elapsed, share)



class _BoundBlock:
    """A registry block bound to its validated config and the run's resources.

    Presents the ``can_run`` / ``process`` surface the runner expects, building
    the block's enhancer lazily in ``process`` so construction stays cheap and
    dependency-free. Every enhancer honours ``enhance(analysis) -> Analysis``,
    so one wrapper serves all blocks and the dependency wiring stays in the
    ``blocks`` registry.
    """

    __slots__ = ("_spec", "_config", "_ctx")

    def __init__(self, spec: BlockSpec, config: Any, ctx: BuildContext) -> None:
        self._spec = spec
        self._config = config
        self._ctx = ctx

    def name(self) -> str:
        return self._spec.id

    def can_run(self, analysis: Analysis) -> bool:
        return self._spec.can_run(analysis)

    def process(self, analysis: Analysis) -> Analysis:
        return self._spec.build_enhancer(self._config, self._ctx).enhance(analysis)


def _build_step(
    block_id: str,
    config: Any,
    logger: logging.Logger,
    lotus_store: Optional[LotusStore],
    library_store: Optional[SpectralLibraryStore],
) -> _BoundBlock:
    """Bind a registry block to its config and shared resources.

    Dependency wiring lives in each ``BlockSpec.build_enhancer`` (see
    ``pipeline/blocks.py``); the runner just supplies the shared
    ``BuildContext``.
    """
    spec = BLOCKS_BY_ID[block_id]
    logger.debug("Binding block %s", block_id)
    ctx = BuildContext(
        logger=logger, lotus_store=lotus_store, spectral_library_store=library_store
    )
    return _BoundBlock(spec, config, ctx)
