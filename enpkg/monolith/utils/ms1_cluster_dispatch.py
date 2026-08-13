"""Cluster-aware MS1 dispatch: let satellites inherit their anchor's molecule.

When the MS1 adduct-graph enhancer has run, every feature carries a resolved role
(``anchor`` / ``satellite`` / ``singleton``). Anchors and singletons are the
base-ion candidates that get their own LOTUS mass-search; a **satellite** is,
by construction, a non-base adduct (``[M+Na]+``, ``[M+K]+``, ``[M+H-H2O]+`` …) of
the *same* molecule as its cluster's anchor. Re-searching LOTUS for a satellite is
therefore redundant — instead it inherits the anchor's resolved molecule
candidates, re-cast under the satellite's own resolved adduct form.

This module is pure (no DB / config / matchms), so the inheritance logic can be
unit-tested fast; the MS1 enhancer calls it after the anchor/singleton search.
See ``docs/MS1_ENHANCER.md`` and the graph roles in
``enpkg/monolith/utils/ms1_adduct_graph.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from logging import Logger
from typing import TYPE_CHECKING, Optional

from enpkg.monolith.data.ms1_data_classes import ChemicalAdduct

if TYPE_CHECKING:  # avoid importing the matchms-backed spectrum class at runtime
    from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum


def inherit_satellite_annotations(
    spectra: Sequence["AnnotatedSpectrum"],
    logger: Optional[Logger] = None,
) -> None:
    """Give each satellite its anchor's resolved molecule, re-cast under its own form.

    For every ``satellite`` spectrum, look up its cluster's ``anchor`` (same
    ``ms1_cluster_id``), take the anchor's **base-form** formula groups — the
    ``ChemicalAdduct``s whose recipe equals the anchor's resolved (base) recipe, i.e.
    the anchor's actual molecule candidates, not its coincidental non-base
    hypotheses — and rebuild them as ``ChemicalAdduct``s under the satellite's own
    ``ms1_assigned_recipe``. Mutates ``spectrum.ms1_annotations`` in place; anchors,
    singletons, and un-clustered spectra are left untouched.

    A satellite whose cluster has no anchor, or with no assigned recipe, is given an
    empty annotation list (defensive — peeling guarantees one anchor per cluster, so
    this should not occur in practice).
    """
    anchors: dict[int|None, "AnnotatedSpectrum"] = {
        s.ms1_cluster_id: s for s in spectra if s.ms1_cluster_role == "anchor"
    }

    n_inherited = 0
    for spectrum in spectra:
        if spectrum.ms1_cluster_role != "satellite":
            continue
        anchor = anchors.get(spectrum.ms1_cluster_id)
        if anchor is None or spectrum.ms1_assigned_recipe is None:
            spectrum.ms1_annotations = []
            continue
        base_recipe = anchor.ms1_assigned_recipe
        molecule_groups = [
            adduct.lotus
            for adduct in anchor.ms1_annotations
            if adduct.recipe == base_recipe
        ]
        spectrum.ms1_annotations = [
            ChemicalAdduct(lotus=group, recipe=spectrum.ms1_assigned_recipe)
            for group in molecule_groups
        ]
        n_inherited += 1

    if logger is not None and n_inherited:
        logger.info(
            "MS1 cluster dispatch: %d satellites inherited their anchor's molecule "
            "(no redundant LOTUS search).",
            n_inherited,
        )
