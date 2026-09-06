"""Shared helpers for the per-block log summary functions.

The block summary functions in :mod:`enpkg.monolith.pipeline.blocks` produce the
post-run report.  Small, reusable helpers that several summaries can share live
here so that ``blocks.py`` stays focused on the block registry itself.
"""
from __future__ import annotations

from typing import Optional

import numpy as np


def has_nonzero_scores(scores: Optional[np.ndarray]) -> bool:
    """True if a propagated NPC score vector exists and is not all-zero.

    The weights step writes a score vector onto every spectrum, but spectra
    that received no signal (directly or via label propagation across the
    network) keep an all-zero vector.  This separates the spectra reranking
    actually touched from the untouched ones.
    """
    return scores is not None and bool(np.any(scores))
