"""Select which features the MS2 enhancer annotates, from MS1 adduct-graph roles.

Spectral libraries (GNPS, MassBank, MoNA, NIST, and the in-silico ISDB) are
overwhelmingly acquired as the **base ion** — ``[M+H]+`` in positive mode,
``[M-H]-`` in negative. A feature the MS1 adduct graph resolved as a *non-base*
adduct (a "satellite" such as ``[M+Na]+`` / ``[M+K]+``) therefore has nothing to
gain from spectral matching: its precursor m/z will not line up with a base-ion
library entry, so running the (expensive) MS/MS cosine on it is at best wasted
work and at worst a spurious cross-adduct match. The same molecule's base ion is
already represented by the cluster's **anchor**.

This module is kept pure and dependency-light (no ``matchms`` / DB imports) so the
selection logic can be unit-tested fast; the MS2 enhancer delegates to it.

See ``docs/MS2_ENHANCER.md`` and the graph roles in
``enpkg/monolith/utils/ms1_adduct_graph.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from logging import Logger
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid importing the matchms-backed spectrum class at runtime
    from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum

# The values ``MSEnhancerConfig.ms2_adduct_filter`` may take (mirror the Literal
# declared there). "non_satellite" is the recommended default.
MS2_ADDUCT_FILTER_MODES: tuple[str, ...] = ("non_satellite", "base_only", "all")


def select_spectra_for_ms2(
    spectra: Sequence["AnnotatedSpectrum"],
    mode: str,
    logger: Optional[Logger] = None,
) -> list["AnnotatedSpectrum"]:
    """Return the subset of ``spectra`` the MS2 enhancer should annotate.

    Selection is driven by each spectrum's ``ms1_cluster_role`` (stamped by the
    MS1 graph enhancer): ``"anchor"`` is the base ion (``[M+H]+`` / ``[M-H]-``),
    ``"satellite"`` is a resolved non-base adduct, ``"singleton"`` is a feature
    with no adduct relationships (its form is unknown — it may itself be the base
    ion), and ``None`` means the graph did not run.

    Modes
    -----
    ``"all"``
        Annotate every feature — no gating (legacy behaviour).
    ``"base_only"``
        Annotate only resolved base-ion anchors. Strictest reading of
        "match only ``[M+H]+`` / ``[M-H]-``".
    ``"non_satellite"`` (recommended default)
        Annotate anchors *and* singletons — i.e. skip only the features
        positively resolved as another adduct. Keeps the base-ion candidates
        while still dropping the redundant ``[M+Na]+`` / ``[M+K]+`` re-detections.

    If **no** roles are stamped (the ``ms1_graph`` block did not run before
    ``ms2``), gating is impossible, so every feature is annotated and a warning is
    logged.
    """
    if mode == "all":
        return list(spectra)

    if mode not in MS2_ADDUCT_FILTER_MODES:
        raise ValueError(
            f"Unknown ms2_adduct_filter mode {mode!r}; expected one of "
            f"{MS2_ADDUCT_FILTER_MODES}"
        )

    roles_present = any(s.ms1_cluster_role is not None for s in spectra)
    if not roles_present:
        if logger is not None:
            logger.warning(
                "MS2 adduct gating %r was requested but no MS1 cluster roles are "
                "stamped on the spectra (did the 'ms1_graph' block run before "
                "'ms2'?); annotating all %d features.",
                mode,
                len(spectra),
            )
        return list(spectra)

    if mode == "base_only":
        selected = [s for s in spectra if s.ms1_cluster_role == "anchor"]
    else:  # "non_satellite"
        selected = [s for s in spectra if s.ms1_cluster_role != "satellite"]

    if logger is not None:
        logger.info(
            "MS2 adduct gating %r: annotating %d / %d features "
            "(%d skipped as non-base adducts).",
            mode,
            len(selected),
            len(spectra),
            len(spectra) - len(selected),
        )
    return selected
