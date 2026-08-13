"""Pure implementation of the mzAdan-style MS1 adduct-relationship graph.

The graph relates LC-MS **features** (each a single precursor m/z) that look like
different ionization products of the *same* neutral molecule: ``[M+H]+`` and its
``[M+Na]+``, ``[M+K]+``, ``[M+H-H2O]+``, ``[2M+H]+`` and so on. Nodes are features;
a directed edge ``X -> Y`` records that *if* X is recipe ``Ra`` *then* Y is the
predicted recipe ``Rb`` of the same molecule (within a mass tolerance). Weakly
connected components are candidate single-molecule clusters, and within each
cluster the feature that — taken as the base ``[M+H]+``/``[M-H]-`` form — explains
the most co-clustered intensity is chosen as the molecule's true base ion
(the "anchor").

This module is deliberately free of pydantic / config / database dependencies
(same convention as ``label_propagation_algorithm.py``): it takes plain
``GraphPeak`` tuples and ``AdductRecipe`` objects, so the whole algorithm is unit
testable against the paper's worked examples. See ``docs/MS1_GRAPH_ENHANCER.md``.
"""

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple, Optional

import networkx as nx

from enpkg.monolith.data.ms1_data_classes.adduct_class import AdductRecipe


class GraphPeak(NamedTuple):
    """One MS1 peak: a feature's precursor m/z plus the attributes ranking needs."""

    feature_id: int
    mz: float
    retention_time: float
    intensity: float


@dataclass
class FeatureClusterResult:
    """Per-feature outcome of the graph resolution.

    ``role`` is one of ``"anchor"`` (a molecule's base ion), ``"satellite"`` (a
    resolved adduct of an anchor), or ``"singleton"`` (a feature with no adduct
    relationships at all). ``assigned_recipe`` is the ionization form the feature
    was resolved to (``None`` for singletons). Cluster-level indices are ``None``
    for singletons; for a lone anchor (a base ion with no satellites) the
    connectivity is 1.
    """

    feature_id: int
    role: str
    cluster_id: Optional[int] = None
    assigned_recipe: Optional[AdductRecipe] = None
    cluster_connectivity: Optional[int] = None
    cluster_intensity_coverage: Optional[float] = None
    cluster_count_coverage: Optional[float] = None


# Edge attribute key holding the list of (source_recipe, target_recipe, mass_error)
# interpretations. A single ordered pair (X, Y) can be adduct-consistent under more
# than one recipe pairing (the paper's "bidirectional arrow" ambiguities), so we
# keep them all on one directed edge rather than collapsing to a single reading.
_INTERPRETATIONS = "interpretations"


class _Interpretation(NamedTuple):
    source_recipe: AdductRecipe
    target_recipe: AdductRecipe
    mass_error: float


def build_adduct_graph(
    peaks: list[GraphPeak],
    recipes: list[AdductRecipe],
    tol: float,
    *,
    rt_tolerance: Optional[float] = None,
) -> nx.DiGraph:
    """Build the directed adduct-relationship graph over ``peaks``.

    For every peak X and recipe ``Ra`` we back out the neutral mass X would have as
    ``Ra`` (``Ra.compute_neutral_mass``), then for every *other* recipe ``Rb`` we
    predict where the same molecule's ``Rb`` ion would fall and look for a real peak
    Y there (a binary search over the m/z-sorted peaks). A hit adds the directed
    edge ``X -(Ra -> Rb)-> Y``.

    Parameters
    ----------
    peaks:
        The features to relate. ``feature_id`` values must be unique.
    recipes:
        The (small) graph recipe set for the analysis ionization mode.
    tol:
        m/z match tolerance in Da (reuses the enhancer's ``parent_mz_tol``).
    rt_tolerance:
        If given, an edge is kept only when the two features co-elute within this
        many minutes (mzAdan's optional chromatographic validation). ``None``
        disables the check — pure mass, mzAdan's default.
    """
    graph = nx.DiGraph()
    for peak in peaks:
        graph.add_node(
            peak.feature_id,
            mz=peak.mz,
            retention_time=peak.retention_time,
            intensity=peak.intensity,
        )

    # Sorted m/z index for range lookups: parallel arrays keep it dependency-free.
    ordered = sorted(peaks, key=lambda p: p.mz)
    sorted_mz = [p.mz for p in ordered]
    rt_by_id = {p.feature_id: p.retention_time for p in peaks}

    for x in peaks:
        for recipe_a in recipes:
            neutral_mass = recipe_a.compute_neutral_mass(x.mz)
            if neutral_mass <= 0:
                # Negative neutral mass is physically impossible; skip this recipe.
                continue
            for recipe_b in recipes:
                if recipe_b == recipe_a:
                    # Same form => predicts X's own m/z; not an adduct relationship.
                    continue
                predicted_mz = recipe_b.compute_adduct_mass(neutral_mass)
                lo = bisect_left(sorted_mz, predicted_mz - tol)
                hi = bisect_right(sorted_mz, predicted_mz + tol)
                for y in ordered[lo:hi]:
                    if y.feature_id == x.feature_id:
                        continue
                    if rt_tolerance is not None and (
                        abs(rt_by_id[x.feature_id] - rt_by_id[y.feature_id]) > rt_tolerance
                    ):
                        continue
                    _add_interpretation(
                        graph,
                        x.feature_id,
                        y.feature_id,
                        _Interpretation(recipe_a, recipe_b, y.mz - predicted_mz),
                    )
    return graph


def _add_interpretation(
    graph: nx.DiGraph, source: int, target: int, interpretation: _Interpretation
) -> None:
    """Append one interpretation to the directed edge (creating the edge if new)."""
    if graph.has_edge(source, target):
        graph[source][target][_INTERPRETATIONS].append(interpretation)
    else:
        graph.add_edge(source, target, **{_INTERPRETATIONS: [interpretation]})


def explained_reachability(
    graph: nx.DiGraph,
    root: int,
    base_recipe: AdductRecipe,
    allowed: Optional[set[int]] = None,
) -> dict[int, AdductRecipe]:
    """Nodes ``root`` can consistently explain if it is the base ``[M+H]+`` form.

    This is a *constrained* breadth-first search (BFS). BFS is the standard graph
    walk that starts at one node and visits its neighbours, then their neighbours,
    fanning outward level by level (a FIFO queue); here the *set* of nodes reached
    is what matters, not the order. The constraint: we leave a node only along an
    edge that was derived assuming that node's already-committed recipe, and each
    step commits the neighbour to that edge's target recipe. This enforces one
    self-consistent chain of ionization interpretations — a feature can't be read
    as ``[M+H]+`` in one step and ``[M+H-H2O]+`` in another.

    Worked example (creatine, from the paper): the root 132 is committed as
    ``[M+H]+``; the 132->114 edge was derived with source ``[M+H]+`` so it is
    followable and commits 114 to ``[M+H-H2O]+``; the 114->136 edge was derived
    with source ``[M+H-H2O]+`` so it is followable *because it matches 114's now
    committed recipe*; an edge that had assumed 114 was base ``[M+H]+`` is rejected.

    Parameters
    ----------
    allowed:
        If given, the walk may only step onto nodes in this set. Peeling passes
        this to keep an anchor from claiming features already assigned to an
        earlier-peeled molecule.

    Returns
    -------
    dict
        ``{feature_id: assigned_recipe}`` for every node reachable under the rule,
        always including ``root`` itself mapped to ``base_recipe``.
    """
    assigned: dict[int, AdductRecipe] = {root: base_recipe}
    queue: deque[int] = deque([root])  # FIFO => breadth-first order
    while queue:
        current = queue.popleft()
        current_recipe = assigned[current]
        for _, neighbor, data in graph.out_edges(current, data=True):
            if neighbor in assigned:
                continue  # already committed to a recipe; don't revisit
            if allowed is not None and neighbor not in allowed:
                continue  # claimed by an earlier-peeled molecule
            # Follow this edge only via an interpretation consistent with the
            # recipe we've already committed `current` to. First match wins
            # (traversal-order tie-break for the rare doubly-ambiguous chain).
            for interpretation in data[_INTERPRETATIONS]:
                if interpretation.source_recipe == current_recipe:
                    assigned[neighbor] = interpretation.target_recipe
                    queue.append(neighbor)
                    break
    return assigned


def _best_anchor(
    graph: nx.DiGraph, candidates: set[int], base_recipe: AdductRecipe
) -> tuple[int, dict[int, AdductRecipe]]:
    """Return the highest-explained-intensity ``(anchor, assignments)`` among
    ``candidates``, with the walk restricted to ``candidates``.

    Ties break on the larger explained set, then the lowest feature id — iterating
    ``sorted(candidates)`` with a strict ``>`` makes that deterministic (set
    iteration order is otherwise undefined).
    """
    best_node: Optional[int] = None
    best_key: Optional[tuple[float, int]] = None
    best_assignments: dict[int, AdductRecipe] = {}
    for node in sorted(candidates):
        reachable = explained_reachability(graph, node, base_recipe, allowed=candidates)
        explained_intensity = sum(graph.nodes[n]["intensity"] for n in reachable)
        key = (explained_intensity, len(reachable))
        if best_key is None or key > best_key:
            best_key = key
            best_node = node
            best_assignments = reachable
    return best_node, best_assignments


def _peel_component(
    graph: nx.DiGraph, members: set[int], base_recipe: AdductRecipe
) -> list[tuple[int, dict[int, AdductRecipe]]]:
    """Extract every molecule from one component, strongest first.

    Repeatedly take the best anchor among the *remaining* features (its walk
    restricted to what's left, so it can't claim an already-peeled molecule's
    ions), record it and the ions it explains as one sub-cluster, remove them, and
    continue until the component is exhausted. A feature that can only explain
    itself becomes its own single-member sub-cluster (a lone base ion). Because
    every pass removes at least its anchor, this always terminates and every
    feature ends up in exactly one sub-cluster — nothing is left ``unexplained``.

    Returns the sub-clusters as ``[(anchor, {node: recipe}), ...]``.
    """
    remaining = set(members)
    subclusters: list[tuple[int, dict[int, AdductRecipe]]] = []
    while remaining:
        anchor, assignments = _best_anchor(graph, remaining, base_recipe)
        subclusters.append((anchor, assignments))
        remaining -= set(assignments)
    return subclusters


def resolve_adduct_graph(
    peaks: list[GraphPeak],
    recipes: list[AdductRecipe],
    base_recipe: AdductRecipe,
    *,
    tol: float,
    rt_tolerance: Optional[float] = None,
) -> tuple[nx.DiGraph, dict[int, FeatureClusterResult]]:
    """Build the adduct graph and resolve each feature's ionization role.

    Convenience wrapper: builds the graph from ``peaks`` then calls
    :func:`resolve_clusters`. Returns the graph plus
    ``{feature_id: FeatureClusterResult}`` covering every input peak.
    """
    graph = build_adduct_graph(peaks, recipes, tol, rt_tolerance=rt_tolerance)
    return graph, resolve_clusters(graph, base_recipe)


def resolve_clusters(
    graph: nx.DiGraph, base_recipe: AdductRecipe
) -> dict[int, FeatureClusterResult]:
    """Resolve a built graph into per-feature cluster roles by **peeling**.

    Each multi-node weakly-connected component is peeled (see
    :func:`_peel_component`) into one or more sub-clusters — one per molecule — so
    a single component yields several anchors rather than one anchor plus a pile of
    unexplained features. Size-1 components are singletons. Every feature ends up
    ``anchor``, ``satellite``, or ``singleton``; ``unexplained`` no longer occurs.

    Cluster indices mirror mzAdan's CGC/CIC/CCC and are computed **per sub-cluster**
    (i.e. per resolved molecule), not per raw component:

    - ``cluster_connectivity`` (CGC) — features in the sub-cluster,
    - ``cluster_intensity_coverage`` (CIC) — sub-cluster intensity / total intensity,
    - ``cluster_count_coverage`` (CCC) — sub-cluster size / total feature count.
    """
    total_intensity = sum(graph.nodes[n]["intensity"] for n in graph.nodes) or 1.0
    total_count = graph.number_of_nodes() or 1

    results: dict[int, FeatureClusterResult] = {}
    cluster_id = 0
    for component in nx.weakly_connected_components(graph):
        if len(component) == 1:
            (feature_id,) = tuple(component)
            results[feature_id] = FeatureClusterResult(
                feature_id=feature_id, role="singleton"
            )
            continue

        for anchor, assignments in _peel_component(graph, set(component), base_recipe):
            members = set(assignments)
            connectivity = len(members)
            intensity_coverage = (
                sum(graph.nodes[n]["intensity"] for n in members) / total_intensity
            )
            count_coverage = len(members) / total_count
            for feature_id in members:
                results[feature_id] = FeatureClusterResult(
                    feature_id=feature_id,
                    role="anchor" if feature_id == anchor else "satellite",
                    cluster_id=cluster_id,
                    assigned_recipe=assignments.get(feature_id),
                    cluster_connectivity=connectivity,
                    cluster_intensity_coverage=intensity_coverage,
                    cluster_count_coverage=count_coverage,
                )
            cluster_id += 1

    return results
