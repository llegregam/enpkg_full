"""Central registry of pipeline blocks.

Adding a block requires exactly one entry here.  Each entry bundles everything
the pipeline and its front-ends need to know about a block:

- display label and description (for the sidebar checkboxes / tooltips)
- enhancer factory and Pydantic config class (for the runner + forms)
- selection dependencies (which other blocks must also be selected)
- ordering constraints (which blocks must run before or after it)
- shared resources the enhancer needs (``requires``)
- **log_summary function** (for the post-run report in the log file)

Shared EnhancerConfig classes (e.g. MSEnhancerConfig for both MS1 and MS2)
are supported by declaring a shared key and listing the associated blocks in
a constant at the bottom of this file.

``BLOCKS`` is computed, not written by hand: ``_BUILTIN_BLOCKS`` declares the
blocks and ``order_blocks`` sorts them into execution order from their
``after``/``before`` constraints.  Blocks with no constraint between them keep
their ``_BUILTIN_BLOCKS`` order, so the sequence stays reproducible.

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

import heapq
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type

import networkx as nx
from pydantic import BaseModel

from enpkg.monolith.configuration.ms1_graph_enhancer_config import MS1GraphEnhancerConfig
from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.enhancers.ms1_enhancer import MS1Enhancer
from enpkg.monolith.enhancers.ms1_graph_enhancer import MS1GraphEnhancer
from enpkg.monolith.enhancers.ms2_enhancer import Ms2Enhancer
from enpkg.monolith.enhancers.network_enhancer import NetworkEnhancer
from enpkg.monolith.enhancers.sirius_enhancer import SiriusEnhancer
from enpkg.monolith.enhancers.taxa_enhancer import TaxaEnhancer
from enpkg.monolith.enhancers.weights_enhancer import WeightsEnhancer
from enpkg.monolith.loaders.database_loader import DBLoader
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.pipeline.log_utils import has_nonzero_scores

# Callable signature for every block's post-run log summary.
# Each function receives the shared logger and the final Analysis, and should
# log the relevant metrics at INFO level.
# So SummaryFn is shorthand for "any function that accepts a Logger
# and Analysis and returns None."
SummaryFn = Callable[[logging.Logger, Analysis], None]


@dataclass(frozen=True)
class BuildContext:
    """Shared resources a block's enhancer may need, injected by the runner.

    A block only reads the fields it needs (e.g. taxonomical/networking use
    none of them). ``db_loader`` and ``lotus_store`` are built once per run and
    reused across blocks.

    A field is only populated when some selected block asked for it through
    ``BlockSpec.requires``; otherwise it stays ``None``.  A block that reads a
    resource it did not declare will therefore find nothing there.
    """

    logger: logging.Logger
    db_loader: Optional[DBLoader] = None
    lotus_store: Optional[LotusStore] = None


# Resource names a block may ask for via ``BlockSpec.requires``; each maps to a
# field of ``BuildContext`` the runner fills in when at least one selected block
# requests it. ``lotus_store`` implies ``db_loader`` — both are built from the
# same MSEnhancerConfig, and the store reads the DuckDB file the loader manages.
KNOWN_RESOURCES = frozenset({"db_loader", "lotus_store"})


# A block's enhancer factory: ``(validated_config, BuildContext) -> Enhancer``.
BuildFn = Callable[[Any, BuildContext], Enhancer]
# A block's applicability guard: ``(Analysis) -> bool``.
CanRunFn = Callable[[Analysis], bool]


@dataclass(frozen=True)
class BlockSpec:
    """Immutable descriptor for a single pipeline block.

    The registry is the single source of truth. Each block carries
    ``build_enhancer`` (how to construct its enhancer from the run's shared
    resources) and ``can_run`` (the applicability guard); the runner wraps these
    into a uniform step. The ``log_summary`` callable is invoked after a
    successful run to append a per-block section to the run log.

    ``depends_on`` and ``after``/``before`` answer different questions and are
    not interchangeable:

    * ``depends_on`` is about **selection** — "these blocks must also be ticked,
      or I cannot run at all". A missing entry skips the block.
    * ``after`` / ``before`` are about **order** — "whatever else is selected,
      I run later/earlier than these". They constrain the sort in
      ``order_blocks`` and are ignored when the named block is not selected.

    A block usually needs both when it consumes another's output: ``weights``
    requires ``network`` to be selected *and* to have run first.

    Ordering constraints name blocks that may not exist (a plugin ordering
    against a block that isn't installed), so an unknown id in ``after`` or
    ``before`` is silently dropped. An unknown id in ``depends_on`` is a real
    missing requirement and skips the block at runtime.
    """

    id: str
    label: str
    build_enhancer: BuildFn
    can_run: CanRunFn
    config_cls: Optional[Type[BaseModel]]
    log_summary: SummaryFn
    description: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    after: tuple[str, ...] = field(default_factory=tuple)
    before: tuple[str, ...] = field(default_factory=tuple)
    requires: frozenset[str] = field(default_factory=frozenset)


class BlockOrderError(ValueError):
    """Raised when ordering constraints cannot be satisfied."""


def order_blocks(specs: Sequence[BlockSpec]) -> list[BlockSpec]:
    """Sort blocks into execution order from their ``after``/``before`` edges.

    Blocks that no constraint separates keep their order in ``specs``, which is
    what makes a run reproducible: two installations holding the same blocks
    produce the same sequence regardless of the order they were collected in.

    Args:
        specs: The blocks to order. Their position here is the tie-break.

    Raises:
        BlockOrderError: If the constraints contain a cycle, naming the blocks
            still unplaced when the sort stalled.

    Returns:
        The same blocks, ordered so that every satisfiable constraint holds.
    """
    position = {spec.id: i for i, spec in enumerate(specs)}
    successors: dict[str, set[str]] = {spec.id: set() for spec in specs}
    indegree: dict[str, int] = {spec.id: 0 for spec in specs}

    def add_edge(earlier: str, later: str) -> None:
        # A constraint naming a block that is not present has nothing to order
        # against; a duplicate edge must not be counted twice in the indegree.
        if earlier not in position or later not in position:
            return
        if later in successors[earlier]:
            return
        successors[earlier].add(later)
        indegree[later] += 1

    for spec in specs:
        for earlier in spec.after:
            add_edge(earlier, spec.id)
        for later in spec.before:
            add_edge(spec.id, later)

    # Kahn's algorithm over a heap of source positions: of the blocks that are
    # ready, always take the one declared earliest.
    ready = [position[bid] for bid, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)

    ordered: list[BlockSpec] = []
    while ready:
        spec = specs[heapq.heappop(ready)]
        ordered.append(spec)
        for successor in successors[spec.id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(ready, position[successor])

    if len(ordered) != len(specs):
        unplaced = sorted(bid for bid, degree in indegree.items() if degree > 0)
        raise BlockOrderError(
            "Block ordering constraints contain a cycle; could not place: "
            + ", ".join(unplaced)
        )
    return ordered


# ---------------------------------------------------------------------------
# Enhancer factories + applicability guards
#
# Each enhancer honours the uniform ``enhance(analysis) -> Analysis`` contract,
# so a block is fully described by (how to build its enhancer, when it can run).
# ---------------------------------------------------------------------------

def _build_taxonomical(config: Any, ctx: BuildContext) -> Enhancer:
    return TaxaEnhancer()


def _build_network(config: Any, ctx: BuildContext) -> Enhancer:
    return NetworkEnhancer(configuration=config)


def _build_ms1_graph(config: Any, ctx: BuildContext) -> Enhancer:
    return MS1GraphEnhancer(config, ctx.logger)


def _build_ms1(config: Any, ctx: BuildContext) -> Enhancer:
    return MS1Enhancer(config, ctx.logger, ctx.lotus_store)


def _build_ms2(config: Any, ctx: BuildContext) -> Enhancer:
    return Ms2Enhancer(config, ctx.logger, ctx.db_loader, ctx.lotus_store)


def _build_sirius(config: Any, ctx: BuildContext) -> Enhancer:
    return SiriusEnhancer(config, ctx.logger)


def _build_weights(config: Any, ctx: BuildContext) -> Enhancer:
    return WeightsEnhancer(config, ctx.logger, ctx.lotus_store)


def _has_spectra(analysis: Analysis) -> bool:
    return len(analysis.spectra) > 0


def _has_source_taxon(analysis: Analysis) -> bool:
    return analysis.has_source_taxon


def _has_spectra_and_network(analysis: Analysis) -> bool:
    return len(analysis.spectra) > 0 and analysis.molecular_network is not None


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


def _log_ms1_graph(logger: logging.Logger, analysis: Analysis) -> None:
    """Summarise the MS1 adduct-relationship graph resolution.

    Reads the per-spectrum ``ms1_cluster_role`` stamps and the attached
    ``analysis.ms1_adduct_graph``; reports how features were resolved into
    clusters (anchors / satellites / unexplained) versus left as singletons.
    """
    graph = analysis.ms1_adduct_graph
    if graph is None:
        logger.info("    (no MS1 adduct graph on analysis)")
        return
    roles = [s.ms1_cluster_role for s in analysis.spectra]
    n_clusters = len(
        {s.ms1_cluster_id for s in analysis.spectra if s.ms1_cluster_id is not None}
    )
    logger.info("    Features               : %d", len(analysis.spectra))
    logger.info("    Adduct clusters        : %d", n_clusters)
    logger.info("    Anchors                : %d", roles.count("anchor"))
    logger.info("    Satellites             : %d", roles.count("satellite"))
    logger.info("    Unexplained            : %d", roles.count("unexplained"))
    logger.info("    Singletons             : %d", roles.count("singleton"))


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
    """Summarise SIRIUS structure-identification annotations.

    Counts how many spectra received at least one SIRIUS annotation and the total
    number of candidate structures attached across all spectra (reads
    ``AnnotatedSpectrum.sirius_annotations``, populated by ``attach_sirius_annotations``),
    then the CANOPUS class-prediction coverage (``canopus_classification``, populated by
    ``attach_canopus_classifications``).

    CANOPUS coverage is reported as a ratio on purpose. It only classifies features SIRIUS
    could assign a molecular formula to, so a figure well below the spectrum count is
    normal; stating the denominator stops it reading as data loss.
    """
    n_spectra = len(analysis.spectra)
    n_annotated = sum(1 for s in analysis.spectra if s.has_sirius_annotations())
    total = sum(len(s.sirius_annotations) for s in analysis.spectra)
    logger.info("    Spectra with SIRIUS annotations : %d / %d", n_annotated, n_spectra)
    logger.info("    Total SIRIUS annotations        : %d", total)
    n_classified = sum(1 for s in analysis.spectra if s.has_canopus_classification())
    logger.info("    Spectra with CANOPUS classes    : %d / %d", n_classified, n_spectra)
    if n_classified:
        pathways = Counter(
            s.canopus_classification.pathway.label
            for s in analysis.spectra
            if s.has_canopus_classification() and s.canopus_classification.pathway is not None
        )
        for label, count in pathways.most_common():
            logger.info("        %-28s: %d", label, count)


# Number of example reranked spectra to show per level (MS1 / MS2) in the
# weights log summary. The MS2 examples are drawn changed-first: they sit directly
# under the "N spectra whose top pick changed" count and are read as evidence for
# it, so showing unchanged spectra there makes the report contradict itself.
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
    # Two buckets filled in the one pass. The examples illustrate the "top pick
    # changed" line printed just below them, so changed spectra are shown first and
    # unchanged ones only backfill an otherwise short list. Collecting the first
    # three candidates regardless used to print three identical before/after keys
    # directly under "N / M changed", which read as a broken report. The loop still
    # visits every candidate — n_changed counts all of them, not just the shown ones.
    changed_examples: list[tuple] = []
    unchanged_examples: list[tuple] = []
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
        bucket = changed_examples if changed else unchanged_examples
        if len(bucket) < _WEIGHTS_MAX_EXAMPLES:
            bucket.append((
                spectrum.feature_id,
                spectral_best.short_inchikey,
                spectral_best.score,
                reranked[0],
                "  [changed]" if changed else "  [unchanged]",
            ))
    examples = (changed_examples + unchanged_examples)[:_WEIGHTS_MAX_EXAMPLES]

    logger.info("    %-34s : %d / %d", "MS2 spectra whose top pick changed",
                n_changed, len(ms2_candidates))
    for fid, spectral_ik, score, reranked_ik, flag in examples:
        logger.info("      feature %s: spectral-best %s (score %.3f) -> reranked-best %s%s",
                    fid, spectral_ik, score, reranked_ik, flag)
    if ms2_candidates and not examples:
        logger.info("      (no MS2 candidates could be reranked)")


# ---------------------------------------------------------------------------
# Block registry
#
# Adding a new pipeline block means adding one entry here with all required
# fields (including ``log_summary``). Declare the ordering constraints the block
# genuinely has rather than relying on where the entry sits: ``order_blocks``
# derives the execution order from them, and only falls back to this list's
# order for blocks nothing separates.
# ---------------------------------------------------------------------------

_BUILTIN_BLOCKS: list[BlockSpec] = [
    BlockSpec(
        id="taxonomical",
        label="Taxonomical enrichment",
        build_enhancer=_build_taxonomical,
        can_run=_has_source_taxon,
        config_cls=None,
        log_summary=_log_taxonomical,
        description="Fetches Open Tree of Life matches for the source organism. Requires source_taxon in metadata.",
    ),
    BlockSpec(
        id="network",
        label="Molecular networking",
        build_enhancer=_build_network,
        can_run=_has_spectra,
        config_cls=NetworkEnhancerConfig,
        log_summary=_log_network,
        description="Builds a spectral similarity network from MS/MS spectra.",
    ),
    BlockSpec(
        id="ms1_graph",
        label="MS1 adduct graph",
        build_enhancer=_build_ms1_graph,
        can_run=_has_spectra,
        config_cls=MS1GraphEnhancerConfig,
        log_summary=_log_ms1_graph,
        description="Relates features that are adducts of the same molecule and "
        "resolves each cluster's base ion. Runs before MS1 enhancement.",
        # The MS1 enhancer reads the cluster roles this block stamps to decide
        # which features to search, so it is only useful ahead of ms1.
        before=("ms1",),
    ),
    BlockSpec(
        id="ms1",
        label="MS1 enhancement",
        build_enhancer=_build_ms1,
        can_run=_has_spectra,
        config_cls=MSEnhancerConfig, # Shared with MS2
        log_summary=_log_ms1,
        description="Matches MS1 precursor m/z against adduct libraries. Shares config with MS2. "
        "When the ms1_graph block ran first, only anchors and singletons are searched; each "
        "satellite inherits its cluster anchor's molecule under its own adduct form (no redundant "
        "search). Without the graph, every feature is searched.",
        requires=frozenset({"db_loader", "lotus_store"}),
    ),
    BlockSpec(
        id="ms2",
        label="MS2 enhancement",
        build_enhancer=_build_ms2,
        can_run=_has_spectra,
        config_cls=MSEnhancerConfig, # Shared with MS1
        log_summary=_log_ms2,
        description="Matches MS/MS spectra against spectral databases (ISDB).",
        requires=frozenset({"db_loader", "lotus_store"}),
    ),
    BlockSpec(
        id="sirius",
        label="Sirius",
        build_enhancer=_build_sirius,
        can_run=_has_spectra,
        config_cls=SiriusEnhancerConfig,
        log_summary=_log_sirius,
        description="Runs Sirius for structure identification.",
    ),
    BlockSpec(
        id="weights",
        label="Weights / reranking",
        build_enhancer=_build_weights,
        can_run=_has_spectra_and_network,
        config_cls=ReweightingConfig,
        log_summary=_log_weights,
        description="Reranks annotations using taxonomic and chemical consistency. Requires the molecular network.",
        depends_on=("network",),
        # Reranking propagates scores over the network and rewrites the MS1/MS2
        # annotations, so all three have to have produced their output first.
        after=("network", "ms1", "ms2"),
        requires=frozenset({"db_loader", "lotus_store"}),
    ),
]

# Execution order, derived from the constraints above.
BLOCKS: list[BlockSpec] = order_blocks(_BUILTIN_BLOCKS)

BLOCKS_BY_ID: dict[str, BlockSpec] = {b.id: b for b in BLOCKS}


def required_resources(selected_ids: Iterable[str]) -> frozenset[str]:
    """Return the union of the shared resources the selected blocks require.

    Unknown ids are ignored: the caller decides what to do about a block that is
    not in the registry, and it cannot require anything in any case.

    Args:
        selected_ids: Block ids the caller intends to run.

    Returns:
        Resource names drawn from ``KNOWN_RESOURCES``.
    """
    return frozenset().union(
        *(
            BLOCKS_BY_ID[block_id].requires
            for block_id in selected_ids
            if block_id in BLOCKS_BY_ID
        )
    )

# Blocks that share a single EnhancerConfig instance in the unified YAML should be declared here.
# The runner and config I/O will treat these blocks as a group, loading their config from the shared
# key and ensuring they are selected/deselected together in the UI.

# MS shared config
MS_SHARED_BLOCKS = ("ms1", "ms2")
MS_SHARED_KEY = "ms_enhancer"
