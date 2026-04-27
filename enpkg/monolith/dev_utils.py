"""Development and debugging utilities for memory profiling and monitoring."""
from __future__ import annotations

import gc
import logging
import tracemalloc

import psutil


def log_memory_snapshot(logger: logging.Logger, label: str) -> None:
    """Log comprehensive memory snapshot including virtual memory, object counts, and allocations.

    Args:
        logger: Logger instance to write memory info to
        label: Label for this snapshot (e.g., "exp_name_START" or "exp_name_END")
    """
    # Virtual memory stats
    mem = psutil.virtual_memory()
    logger.info(
        "[%s] Virtual Memory: used=%.1fGB, available=%.1fGB, total=%.1fGB, percent=%.1f%%",
        label,
        mem.used / (1024**3),
        mem.available / (1024**3),
        mem.total / (1024**3),
        mem.percent,
    )

    # Object counts by type
    gc.collect()
    obj_counts = {}
    for obj in gc.get_objects():
        obj_type = type(obj).__name__
        obj_counts[obj_type] = obj_counts.get(obj_type, 0) + 1
    top_objects = sorted(obj_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info("[%s] Top 10 object types: %s", label, top_objects)

    # Top memory allocations
    if tracemalloc.is_tracing():
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics("lineno")[:5]
        for stat in top_stats:
            logger.info("[%s] %s", label, stat)
