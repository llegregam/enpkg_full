import networkx as nx
import numpy as np
import pytest

from enpkg.monolith.utils.label_propagation_algorithm import label_propagation_algorithm


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

