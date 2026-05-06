"""Pretty memory snapshots for diagnostic logging.

Used at phase boundaries (MS2 enhance(), batch experiment start/end) to track
where peak memory builds up. The format is intentionally aligned and
greppable (``MEM | <label> | used=... avail=... total=...``).
"""

import logging
from logging import Logger

import psutil


_GB = 1024 ** 3


def log_virtual_memory(logger: Logger, label: str, level: int = logging.INFO) -> None:
    """Log a one-line, aligned ``psutil.virtual_memory`` snapshot.

    Format::

        MEM | <label>                          | used= 4.21 GB (27.0%) | avail=11.30 GB | total=15.51 GB

    Args:
        logger: Destination logger.
        label: Short identifier for the call site (e.g. "MS2 enhance start").
            Truncated/padded to 40 chars in the formatted line.
        level: Log level; defaults to INFO so these lines are visible without
            verbose mode.
    """
    vm = psutil.virtual_memory()
    logger.log(
        level,
        "MEM | %-40s | used=%6.2f GB (%5.1f%%) | avail=%6.2f GB | total=%6.2f GB",
        label,
        vm.used / _GB,
        vm.percent,
        vm.available / _GB,
        vm.total / _GB,
    )
