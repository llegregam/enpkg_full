"""Central registry of pipeline blocks exposed to the GUI.

Adding a new block to the GUI requires exactly one entry here.  Each entry
bundles everything the GUI needs to know about a block:

- display label and description (for the sidebar checkboxes / tooltips)
- PipelineStep subclass and Pydantic config class (for the runner + forms)
- dependency list (which other blocks must also be selected)
- **log_summary function** (for the post-run report in the log file)

Shared EnhancerConfig classes (e.g. MSEnhancerConfig for both MS1 and MS2) 
are supported by declaring a shared key and listing the associated blocks in
a constant at the bottom of this file.

The ``log_summary`` field is *required*. This is intentional: it forces every
new block author to think about what part of the ``Analysis`` their step
modifies and to provide a human-readable summary for the log.  If the block
doesn't yet expose its outputs on the ``Analysis`` model, a minimal stub that
says so is acceptable — see ``_log_sirius`` for an example.

**How to write a ``log_summary`` function:**

1. Accept ``(logger: logging.Logger, analysis: Analysis) -> None``.
2. Read only the fields your block is responsible for (e.g. OTT matches for
   taxonomical, molecular_network for networking).
3. Log at INFO level with aligned labels so the summary block is readable.
4. Handle the case where the field is ``None`` or empty (the block may have
   been skipped by ``can_run``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Type

import networkx as nx
from pydantic import BaseModel

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.gui.log_utils import has_nonzero_scores
from enpkg.monolith.pipeline.base_pipeline_step import PipelineStep
from enpkg.monolith.pipeline.molecular_networking_step import MolecularNetworkingStep
from enpkg.monolith.pipeline.ms1_enhancement_step import MS1EnhancementStep
from enpkg.monolith.pipeline.ms2_enhancement_step import MS2EnrichmentStep
from enpkg.monolith.pipeline.sirius_enhancement_step import SiriusEnhancementStep
from enpkg.monolith.pipeline.taxonomical_enhancement_step import TaxonomicalEnhancementStep
from enpkg.monolith.pipeline.weights_enhancement_step import WeightsEnhancementStep

# Callable signature for every block's post-run log summary.
# Each function receives the shared logger and the final Analysis, and should
# log the relevant metrics at INFO level.
# So SummaryFn is shorthand for "any function that accepts a Logger 
# and Analysis and returns None." 
SummaryFn = Callable[[logging.Logger, Analysis], None]


@dataclass(frozen=True)
class BlockSpec:
    """Immutable descriptor for a single pipeline block.

    Every field except ``description`` and ``depends_on`` is required.  The
    ``log_summary`` callable is invoked after a successful run to append a
    per-block section to the run log.
    """

    id: str
    label: str
    step_cls: Type[PipelineStep]
    config_cls: Optional[Type[BaseModel]]
    log_summary: SummaryFn
    description: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Per-block log summary functions
#
# Each function inspects the ``Analysis`` for the fields that its associated
# pipeline step is responsible for and logs a readable summary.
# ---------------------------------------------------------------------------

def _log_taxonomical(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise Open Tree of Life matches added by the taxonomical step.

    Reads ``analysis.ott_matches``.  If matches exist, logs the best match
    name, OTT id, score, and full lineage breakdown.
    """
    ott = analysis.ott_matches or []
    logger.info("OTT matches: %d", len(ott))
    if not ott:
        return
    best = ott[0]
    logger.info("Best match : %s (OTT %d, score %.3f)",
                best.taxon.name, best.open_tree_taxon_id, best.score)
    if best.lineage is not None:
        # Walk the standard taxonomic ranks and collect non-None values.
        ranks = []
        for rank_name in ("kingdom", "phylum", "klass", "order",
                          "family", "genus", "species"):
            val = getattr(best, rank_name, None)
            if val:
                # 'klass' is used in Python to avoid shadowing the builtin;
                # display as 'class' for readability.
                label = "class" if rank_name == "klass" else rank_name
                ranks.append(f"{label}={val}")
        if ranks:
            logger.info("Lineage: %s", " > ".join(ranks))


def _log_network(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise the molecular similarity network.

    Reads ``analysis.molecular_network`` (a ``networkx.Graph``).  Logs node
    and edge counts, degree statistics, and the number of connected components.
    """
    network = analysis.molecular_network
    if network is None:
        logger.info("    (no molecular network on analysis)")
        return
    logger.info("    Nodes                  : %d", len(network.nodes))
    logger.info("    Edges                  : %d", len(network.edges))
    degrees = [d for _, d in network.degree()]
    if degrees:
        logger.info("    Avg degree             : %.2f", sum(degrees) / len(degrees))
        logger.info("    Max degree             : %d", max(degrees))
    n_components = nx.number_connected_components(network)
    logger.info("    Connected components   : %d", n_components)


def _log_ms1(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise MS1 adduct-matching annotations.

    Counts how many spectra received at least one MS1 annotation and the
    total number of annotations across all spectra.
    """
    n_spectra = len(analysis.spectra)
    n_annotated = sum(1 for s in analysis.spectra if s.has_ms1_annotations())
    total = sum(len(s.ms1_annotations) for s in analysis.spectra)
    logger.info("    Spectra with MS1 annotations : %d / %d", n_annotated, n_spectra)
    logger.info("    Total MS1 annotations        : %d", total)


def _log_ms2(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise MS2 spectral-matching (ISDB) annotations.

    Counts how many spectra received at least one MS2 annotation and the
    total number of annotations across all spectra.
    """
    n_spectra = len(analysis.spectra)
    n_annotated = sum(1 for s in analysis.spectra if s.has_ms2_annotations())
    total = sum(len(s.ms2_annotations) for s in analysis.spectra)
    logger.info("    Spectra with MS2 annotations : %d / %d", n_annotated, n_spectra)
    logger.info("    Total MS2 annotations        : %d", total)


def _log_sirius(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise Sirius structure-identification results.

    Stub: Sirius annotations are not yet surfaced on the Analysis data model
    (``SiriusChemicalAnnotation`` is commented out in ``AnnotatedSpectrum``).
    Expand this once the field is wired in.
    """
    logger.info("    Sirius step completed (annotation summary not yet available)")


# Number of example reranked spectra to show per level (MS1 / MS2) in the
# weights log summary.
_WEIGHTS_MAX_EXAMPLES = 3


def _log_weights(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise the reranking / reweighting step.

    The weights step does not add a field to ``Analysis``; it writes propagated
    NPC pathway/superclass/class score vectors onto each spectrum
    (``ms1_*_scores`` / ``ms2_*_scores``) and exposes the reranked candidates
    through ``AnnotatedSpectrum.get_top_k_lotus_annotation`` (MS1) and
    ``get_top_k_ms2_structures`` (MS2).

    For each level this logs how many spectra were reweighted and shows a few
    example reranked top hits.  MS2 candidates carry their original
    spectral-match score, so the MS2 examples contrast the spectral-best
    structure with the NPC-reranked best and count how many spectra had their
    top pick changed by reranking.  MS1 adducts have no comparable pre-score,
    so the MS1 examples just show the top reranked LOTUS hit.
    """
    spectra = analysis.spectra

    ms1_scored = [s for s in spectra if has_nonzero_scores(s.ms1_pathway_scores)]
    ms2_scored = [s for s in spectra if has_nonzero_scores(s.ms2_pathway_scores)]

    if not ms1_scored and not ms2_scored:
        logger.info(
            "    (no propagated scores found - reranking did not populate any spectra)"
        )
        return

    # ---- MS1 reranking ----
    ms1_candidates = [s for s in spectra if s.has_ms1_annotations()]
    logger.info("    %-34s : %d", "MS1 spectra with annotations", len(ms1_candidates))
    logger.info("    %-34s : %d / %d", "MS1 spectra reweighted (LPA)",
                len(ms1_scored), len(spectra))

    shown = 0
    for spectrum in ms1_candidates:
        if shown >= _WEIGHTS_MAX_EXAMPLES:
            break
        try:
            top = spectrum.get_top_k_lotus_annotation(1)
        except Exception as exc:  # a summary must never crash the run
            logger.debug("MS1 rerank example failed for feature %s: %s",
                         spectrum.feature_id, exc)
            continue
        if not top:
            continue
        lotus = top[0]
        name = (lotus.structure_name_traditional
                or lotus.structure_name_iupac
                or "(unnamed)")
        logger.info("      feature %s -> %s [%s] (%s)",
                    spectrum.feature_id, name, lotus.short_inchikey,
                    lotus.organism_name)
        shown += 1
    if ms1_candidates and shown == 0:
        logger.info("      (no MS1 candidates could be reranked)")

    # ---- MS2 reranking ----
    ms2_candidates = [s for s in spectra if s.has_ms2_annotations()]
    logger.info("    %-34s : %d", "MS2 spectra with annotations", len(ms2_candidates))
    logger.info("    %-34s : %d / %d", "MS2 spectra reweighted (LPA)",
                len(ms2_scored), len(spectra))

    n_changed = 0
    examples: list[tuple] = []
    for spectrum in ms2_candidates:
        try:
            reranked = spectrum.get_top_k_ms2_structures(1)
        except Exception as exc:  # a summary must never crash the run
            logger.debug("MS2 rerank example failed for feature %s: %s",
                         spectrum.feature_id, exc)
            continue
        if not reranked:
            continue
        spectral_best = max(spectrum.ms2_annotations, key=lambda a: a.score)
        changed = reranked[0] != spectral_best.short_inchikey
        if changed:
            n_changed += 1
        if len(examples) < _WEIGHTS_MAX_EXAMPLES:
            examples.append((
                spectrum.feature_id,
                spectral_best.short_inchikey,
                spectral_best.score,
                reranked[0],
                "  [changed]" if changed else "",
            ))

    logger.info("    %-34s : %d / %d", "MS2 spectra whose top pick changed",
                n_changed, len(ms2_candidates))
    for fid, spectral_ik, score, reranked_ik, flag in examples:
        logger.info("      feature %s: spectral-best %s (score %.3f) -> reranked-best %s%s",
                    fid, spectral_ik, score, reranked_ik, flag)
    if ms2_candidates and not examples:
        logger.info("      (no MS2 candidates could be reranked)")


# ---------------------------------------------------------------------------
# Block registry — canonical execution order
#
# The runner iterates this list in order.  Adding a new pipeline block means
# adding one entry here with all required fields (including ``log_summary``).
# ---------------------------------------------------------------------------

BLOCKS: list[BlockSpec] = [
    BlockSpec(
        id="taxonomical",
        label="Taxonomical enrichment",
        step_cls=TaxonomicalEnhancementStep,
        config_cls=None,
        log_summary=_log_taxonomical,
        description="Fetches Open Tree of Life matches for the source organism. Requires source_taxon in metadata.",
    ),
    BlockSpec(
        id="network",
        label="Molecular networking",
        step_cls=MolecularNetworkingStep,
        config_cls=NetworkEnhancerConfig,
        log_summary=_log_network,
        description="Builds a spectral similarity network from MS/MS spectra.",
    ),
    BlockSpec(
        id="ms1",
        label="MS1 enhancement",
        step_cls=MS1EnhancementStep,
        config_cls=MSEnhancerConfig, # Shared with MS2
        log_summary=_log_ms1,
        description="Matches MS1 precursor m/z against adduct libraries. Shares config with MS2.",
    ),
    BlockSpec(
        id="ms2",
        label="MS2 enhancement",
        step_cls=MS2EnrichmentStep,
        config_cls=MSEnhancerConfig, # Shared with MS1
        log_summary=_log_ms2,
        description="Matches MS/MS spectra against spectral databases (ISDB).",
    ),
    BlockSpec(
        id="sirius",
        label="Sirius",
        step_cls=SiriusEnhancementStep,
        config_cls=SiriusEnhancerConfig,
        log_summary=_log_sirius,
        description="Runs Sirius for structure identification.",
    ),
    BlockSpec(
        id="weights",
        label="Weights / reranking",
        step_cls=WeightsEnhancementStep,
        config_cls=ReweightingConfig,
        log_summary=_log_weights,
        description="Reranks annotations using taxonomic and chemical consistency. Requires the molecular network.",
        depends_on=("network",),
    ),
]

BLOCKS_BY_ID: dict[str, BlockSpec] = {b.id: b for b in BLOCKS}

# Blocks that share a single EnhancerConfig instance in the unified YAML should be declared here. 
# The runner and config I/O will treat these blocks as a group, loading their config from the shared 
# key and ensuring they are selected/deselected together in the UI.

# MS shared config
MS_SHARED_BLOCKS = ("ms1", "ms2")
MS_SHARED_KEY = "ms_enhancer"
