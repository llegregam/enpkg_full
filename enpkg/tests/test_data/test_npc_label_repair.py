"""Tests for restoring the NPClassifier labels FragHub's ontology table corrupts."""

import pytest

from enpkg.monolith.loaders.spectral_libraries.npc_labels import (
    NpcLabelRepair,
    npc_label_repair,
    rewritten_form,
)
from enpkg.monolith.rdf.npc_vocabulary import RANKS, npc_vocabulary


@pytest.mark.parametrize(
    ("rank", "cell", "expected"),
    [
        # Rule 1: the substrings "nan" and "none" deleted inside a label.
        ("Superclass", "Ligs", "Lignans"),
        ("Superclass", "Phethrenoids", "Phenanthrenoids"),
        ("Class", "Flavas", "Flavanones"),
        ("Class", "Anthraquis and anthrones", "Anthraquinones and anthrones"),
        ("Class", "Fere and Arborie triterpenoids", "Fernane and Arborinane triterpenoids"),
        # Rule 2: a label containing ", " split into neighbouring labels.
        ("Class", "Lanostane|Tirucallane and Euphane triterpenoids",
         "Lanostane, Tirucallane and Euphane triterpenoids"),
        ("Class", "Carotenoids (C40|β-β)", "Carotenoids (C40, β-β)"),
        # Several labels in one cell: only the corrupted one changes.
        ("Superclass", "Coumarins|Ligs", "Coumarins|Lignans"),
        ("Class", "Flavas|Carotenoids (C40|β-ε)", "Flavanones|Carotenoids (C40, β-ε)"),
    ],
)
def test_corrupted_labels_are_restored(rank, cell, expected):
    assert npc_label_repair().repair(rank, cell)[0] == expected


@pytest.mark.parametrize(
    ("rank", "cell"),
    [
        ("Pathway", "Alkaloids|Terpenoids"),        # genuine labels
        ("Class", "Purine nucleos(t)ides"),         # genuine in the vendored vocabulary
        ("Class", "Carotenoids (C40"),              # a fragment whose partner is missing
        ("Class", "β-β)"),                          # likewise, the other half
        ("Superclass", "Not an NPClassifier term"),
    ],
)
def test_other_labels_are_kept_as_they_are(rank, cell):
    assert npc_label_repair().repair(rank, cell) == (cell, [])


def test_replacements_report_what_changed():
    _, replacements = npc_label_repair().repair("Class", "Flavas|Lanostane|Tirucallane and Euphane triterpenoids")
    assert replacements == [
        ("Flavas", "Flavanones"),
        ("Lanostane|Tirucallane and Euphane triterpenoids",
         "Lanostane, Tirucallane and Euphane triterpenoids"),
    ]


@pytest.mark.parametrize("rank", RANKS)
def test_every_vocabulary_term_survives_a_round_trip(rank):
    """Corrupting any term the way FragHub's table does, then repairing, gives the term
    back -- except where two terms corrupt to the same text, which names neither."""
    terms = npc_vocabulary().labels(rank)
    forms: dict[tuple[str, ...], set[str]] = {}
    for term in terms:
        forms.setdefault(rewritten_form(term), set()).add(term)
    repair = npc_label_repair()
    for term in terms:
        form = rewritten_form(term)
        cell = "|".join(form)
        expected = term if len(forms[form]) == 1 else cell
        assert repair.repair(rank, cell)[0] == expected, term


def test_a_form_two_terms_share_is_not_repaired():
    repair = NpcLabelRepair({"Class": ["Alpha nan", "Alpha none"]})  # both read "Alpha "
    assert repair.repair("Class", "Alpha ") == ("Alpha ", [])


def test_a_genuine_term_is_never_rewritten():
    # "Gamma" is a term in its own right and also one half of "Gamma, Delta".
    repair = NpcLabelRepair({"Class": ["Gamma", "Gamma, Delta"]})
    assert repair.repair("Class", "Gamma|Delta") == ("Gamma|Delta", [])
    assert repair.repair("Class", "Gamma") == ("Gamma", [])
