"""Unit tests for the pure MS1 adduct-relationship graph.

The two ``test_reproduces_paper_*`` tests are golden vectors taken directly from
Stricker et al. 2021 (mzAdan), using the paper's printed m/z and TICex values, so
they pin the algorithm to the reference tool's own worked results.
"""

import networkx as nx
import pytest

from enpkg.monolith.enhancers.graph_adducts import (
    GRAPH_POSITIVE_BASE_RECIPE,
    GRAPH_POSITIVE_RECIPES,
)
from enpkg.monolith.utils.ms1_adduct_graph import (
    GraphPeak,
    _Interpretation,
    build_adduct_graph,
    explained_reachability,
    resolve_adduct_graph,
    resolve_clusters,
)

TOL = 0.01  # Da, matching the paper's 10 mmu default

# Recipes used to hand-build graphs in the peeling tests.
_MH = GRAPH_POSITIVE_RECIPES[0]  # [M+H]+
_MNA = GRAPH_POSITIVE_RECIPES[2]  # [M+Na]+
_MK = GRAPH_POSITIVE_RECIPES[3]  # [M+K]+


def _interp(src, tgt):
    return [_Interpretation(src, tgt, 0.0)]


def _resolve(peaks):
    return resolve_adduct_graph(
        peaks, GRAPH_POSITIVE_RECIPES, GRAPH_POSITIVE_BASE_RECIPE, tol=TOL
    )


# --------------------------------------------------------------------------- edges


def test_edge_detects_potassium_adduct_both_ways():
    # L-proline [M+H]+ and its [M+K]+ are mutually adduct-consistent.
    peaks = [GraphPeak(1, 116.0711, 1.35, 100.0), GraphPeak(2, 154.0264, 1.35, 10.0)]
    graph = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL)
    assert graph.has_edge(1, 2) and graph.has_edge(2, 1)


def test_edge_detects_water_loss():
    # Creatine [M+H]+ and [M+H-H2O]+.
    peaks = [GraphPeak(1, 132.0767, 1.34, 100.0), GraphPeak(2, 114.0674, 1.34, 30.0)]
    graph = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL)
    assert graph.has_edge(1, 2)


def test_edge_detects_dimer():
    peaks = [GraphPeak(1, 116.0711, 1.35, 100.0), GraphPeak(2, 231.1337, 1.35, 10.0)]
    graph = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL)
    assert graph.has_edge(1, 2)


def test_edge_rejects_unrelated_masses():
    # 116.0711 and 200.5 share no adduct relationship in the recipe set.
    peaks = [GraphPeak(1, 116.0711, 1.35, 100.0), GraphPeak(2, 200.5000, 1.35, 100.0)]
    graph = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL)
    assert not graph.has_edge(1, 2) and not graph.has_edge(2, 1)


def test_rt_filter_suppresses_non_coeluting_edge():
    peaks = [GraphPeak(1, 116.0711, 1.35, 100.0), GraphPeak(2, 154.0264, 9.90, 10.0)]
    kept = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL)  # no RT filter
    filtered = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL, rt_tolerance=0.05)
    assert kept.has_edge(1, 2)
    assert not filtered.has_edge(1, 2)


# ---------------------------------------------------------------- reachability rule


def test_reachability_only_follows_recipe_consistent_edges():
    # Star: 116 [M+H]+ explains 154 [M+K]+ and 231 [2M+H]+; neither satellite,
    # hypothesised as the base form, can explain anything (their outgoing edges
    # were derived under [M+K]+ / [2M+H]+, not [M+H]+).
    peaks = [
        GraphPeak(1, 116.0711, 1.35, 59745.0),
        GraphPeak(2, 154.0264, 1.35, 4476.0),
        GraphPeak(3, 231.1337, 1.35, 1066.0),
    ]
    graph = build_adduct_graph(peaks, GRAPH_POSITIVE_RECIPES, TOL)
    assert set(explained_reachability(graph, 1, GRAPH_POSITIVE_BASE_RECIPE)) == {1, 2, 3}
    assert set(explained_reachability(graph, 2, GRAPH_POSITIVE_BASE_RECIPE)) == {2}
    assert set(explained_reachability(graph, 3, GRAPH_POSITIVE_BASE_RECIPE)) == {3}


# ------------------------------------------------------------------ paper vectors


def test_reproduces_paper_proline_star_cluster():
    # Fig. 2b/c: 116.0711 [M+H]+, 154.0264 [M+K]+, 231.1337 [2M+H]+.
    peaks = [
        GraphPeak(1, 116.0711, 1.35, 59745.0),
        GraphPeak(2, 154.0264, 1.35, 4476.0),
        GraphPeak(3, 231.1337, 1.35, 1066.0),
    ]
    _, results = _resolve(peaks)
    assert results[1].role == "anchor"
    assert results[2].role == "satellite"
    assert results[3].role == "satellite"
    assert results[1].assigned_recipe == GRAPH_POSITIVE_BASE_RECIPE
    assert results[2].assigned_recipe.ingredients == {"potassium": 1}
    assert results[3].assigned_recipe.multimer_factor == 2
    # CGC: all three ions belong to one cluster.
    assert results[1].cluster_connectivity == 3


def test_reproduces_paper_creatine_water_loss_and_adduct_cluster():
    # Fig. 2c creatine forms that live in the minimal recipe set:
    # 132.0767 [M+H]+, 114.0674 [M+H-H2O]+, 170.0322 [M+K]+.
    peaks = [
        GraphPeak(1, 132.0767, 1.34, 53734.0),
        GraphPeak(2, 114.0674, 1.34, 28322.0),
        GraphPeak(3, 170.0322, 1.34, 10164.0),
    ]
    _, results = _resolve(peaks)
    assert results[1].role == "anchor"
    assert results[1].assigned_recipe == GRAPH_POSITIVE_BASE_RECIPE
    assert results[2].assigned_recipe.ingredients == {"proton": 1, "water": -1}
    assert results[3].assigned_recipe.ingredients == {"potassium": 1}


# ------------------------------------------------------------- clusters & indices


def test_isolated_feature_is_a_singleton():
    peaks = [
        GraphPeak(1, 116.0711, 1.35, 100.0),
        GraphPeak(2, 154.0264, 1.35, 10.0),
        GraphPeak(3, 500.0000, 5.00, 42.0),  # unrelated to the proline pair
    ]
    _, results = _resolve(peaks)
    assert results[3].role == "singleton"
    assert results[3].cluster_id is None
    assert results[3].assigned_recipe is None


def test_cluster_coverage_indices():
    # One 2-feature cluster + one singleton; total intensity 100+10+40 = 150.
    peaks = [
        GraphPeak(1, 116.0711, 1.35, 100.0),
        GraphPeak(2, 154.0264, 1.35, 10.0),
        GraphPeak(3, 500.0000, 5.00, 40.0),
    ]
    _, results = _resolve(peaks)
    assert results[1].cluster_intensity_coverage == pytest.approx(110.0 / 150.0)
    assert results[1].cluster_count_coverage == pytest.approx(2 / 3)


# ------------------------------------------------------------------ peeling
# These build a graph directly (bypassing mass arithmetic) to exercise peeling's
# multi-anchor extraction in isolation. The coincidental edge 2->3 carries source
# recipe [M+K]+, which is NOT the [M+Na]+ that node 2 holds inside family A, so
# neither anchor's walk crosses it — it only provides weak connectivity.


def test_peeling_extracts_two_molecules_from_one_component():
    g = nx.DiGraph()
    g.add_node(1, intensity=1000.0)  # A · [M+H]+
    g.add_node(2, intensity=100.0)  # A · [M+Na]+
    g.add_node(3, intensity=500.0)  # B · [M+H]+
    g.add_node(4, intensity=50.0)  # B · [M+Na]+
    g.add_edge(1, 2, interpretations=_interp(_MH, _MNA))
    g.add_edge(3, 4, interpretations=_interp(_MH, _MNA))
    g.add_edge(2, 3, interpretations=_interp(_MK, _MH))  # coincidental link

    results = resolve_clusters(g, _MH)

    assert results[1].role == "anchor" and results[2].role == "satellite"
    assert results[3].role == "anchor" and results[4].role == "satellite"
    assert results[1].cluster_id != results[3].cluster_id  # two distinct molecules
    assert all(r.role != "unexplained" for r in results.values())  # nothing stranded
    assert results[1].cluster_connectivity == 2
    assert results[3].cluster_connectivity == 2


def test_peeling_leaves_a_lone_base_ion_as_its_own_anchor():
    g = nx.DiGraph()
    g.add_node(1, intensity=1000.0)  # A · [M+H]+
    g.add_node(2, intensity=100.0)  # A · [M+Na]+
    g.add_node(3, intensity=30.0)  # dragged in, explains only itself
    g.add_edge(1, 2, interpretations=_interp(_MH, _MNA))
    g.add_edge(2, 3, interpretations=_interp(_MK, _MH))  # links but not followable from A

    results = resolve_clusters(g, _MH)

    assert results[1].role == "anchor" and results[2].role == "satellite"
    assert results[3].role == "anchor" and results[3].cluster_connectivity == 1
    assert results[1].cluster_id != results[3].cluster_id
