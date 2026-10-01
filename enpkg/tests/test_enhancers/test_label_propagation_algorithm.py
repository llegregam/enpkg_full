import itertools

import networkx as nx
import numba
import numpy as np
import pytest

from enpkg.monolith.utils.label_propagation_algorithm import label_propagation_algorithm


def _propagate(graph: nx.Graph, features_by_node: dict, order) -> dict:
    """Propagate with the nodes indexed in ``order``; return each node's result by name."""
    features = np.array([features_by_node[node] for node in order], dtype=np.float64)
    propagated = label_propagation_algorithm(
        graph=graph, features=features, node_names=list(order), normalize=False, verbose=False
    )
    return {node: propagated[i] for i, node in enumerate(order)}


def _chain() -> nx.Graph:
    return nx.Graph([("A", "B", {"weight": 1.0}), ("B", "C", {"weight": 1.0}),
                     ("C", "D", {"weight": 1.0})])


def test_label_propagation_algorithm():
    # Create a simple graph
    G = nx.Graph()
    G.add_edge("A", "B", weight=1.0)
    G.add_edge("B", "C", weight=1.0)
    G.add_node("D") # disconnected node

    features = np.array([
        [1.0, 0.0], # A
        [0.0, 0.0], # B
        [0.0, 1.0], # C
        [0.0, 0.0]  # D
    ])

    node_names = ["A", "B", "C", "D"]

    propagated_features = label_propagation_algorithm(
        graph=G,
        features=features,
        node_names=node_names,
        normalize=True,
        verbose=False
    )

    # After propagation, B should have learned from A and C
    assert np.allclose(propagated_features[1], [0.5, 0.5], atol=1e-2)

    # Disconnected node D should remain [0, 0]
    assert np.allclose(propagated_features[3], [0.0, 0.0])

    # A should still strongly favor index 0
    assert propagated_features[0, 0] > propagated_features[0, 1]

    # C should still strongly favor index 1
    assert propagated_features[2, 1] > propagated_features[2, 0]


@pytest.mark.parametrize("order", list(itertools.permutations("ABCD")))
def test_unlabelled_nodes_are_filled_only_from_labelled_neighbours(order):
    """Only A carries a vector, so every node of the chain ends with A's vector.

    A node that receives values during an iteration must not count in that
    iteration's averages, where its values are still zero; otherwise the chain
    ends below 1.0 by an amount that depends on the order nodes are visited in.
    """
    features = {"A": [1.0, 0.0], "B": [0.0, 0.0], "C": [0.0, 0.0], "D": [0.0, 0.0]}

    propagated = _propagate(_chain(), features, order)

    for node in "ABCD":
        assert np.allclose(propagated[node], [1.0, 0.0])


def test_result_does_not_depend_on_node_order():
    """Equal up to rounding: the node order sets the order each weighted sum is added in."""
    features = {"A": [1.0, 0.0], "B": [0.0, 0.0], "C": [0.0, 0.0], "D": [0.0, 1.0]}

    results = [_propagate(_chain(), features, order) for order in itertools.permutations("ABCD")]

    for result in results[1:]:
        for node in "ABCD":
            assert np.allclose(result[node], results[0][node], rtol=0.0, atol=1e-12)


def test_result_does_not_depend_on_thread_count():
    rng = np.random.default_rng(7)
    graph = nx.gnm_random_graph(300, 600, seed=7)
    for u, v in graph.edges:
        graph[u][v]["weight"] = float(rng.uniform(0.7, 1.0))
    features = np.where(rng.uniform(size=(300, 1)) < 0.3, rng.uniform(size=(300, 4)), 0.0)
    names = list(graph.nodes)

    def run() -> np.ndarray:
        return label_propagation_algorithm(
            graph=graph, features=features, node_names=names, normalize=False, verbose=False
        )

    default_threads = numba.get_num_threads()
    try:
        numba.set_num_threads(1)
        single = run()
    finally:
        numba.set_num_threads(default_threads)

    assert np.array_equal(run(), single)


def test_lpa_requires_same_number_of_nodes_and_features():
    G = nx.Graph()
    G.add_edge("A", "B")

    with pytest.raises(AssertionError):
        label_propagation_algorithm(
            graph=G,
            features=np.array([[1.0]]),
            node_names=["A", "B"]
        )

def test_lpa_raises_value_error_if_not_normalized_and_invalid_values():
    G = nx.Graph()
    G.add_node("A")
    features = np.array([[2.0]])

    with pytest.raises(ValueError):
        label_propagation_algorithm(
            graph=G,
            features=features,
            node_names=["A"],
            normalize=False
        )

