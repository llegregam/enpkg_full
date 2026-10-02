"""Unit tests for molecular-formula parsing (element counts, net charge, Hill order)."""

import pytest

from enpkg.monolith.data.molecular_formula import parse_molecular_formula


def test_plain_formula_parses_into_element_counts():
    formula = parse_molecular_formula("C15H20O6")

    assert formula.elements == (("C", 15), ("H", 20), ("O", 6))
    assert formula.charge == 0
    assert formula.hill == "C15H20O6"


def test_two_letter_symbols_and_implicit_counts():
    formula = parse_molecular_formula("C15H23N6O5Se")

    assert formula.elements == (("C", 15), ("H", 23), ("N", 6), ("O", 5), ("Se", 1))


@pytest.mark.parametrize(
    ("text", "charge", "hill"),
    [
        ("C11H12NO+", 1, "C11H12NO+"),
        ("C13H20N2O4P+2", 2, "C13H20N2O4P+2"),
        ("C2H3O2-", -1, "C2H3O2-"),
    ],
)
def test_a_charge_suffix_is_the_net_charge(text, charge, hill):
    formula = parse_molecular_formula(text)

    assert formula.charge == charge
    assert formula.hill == hill


def test_a_charged_formula_keeps_only_element_counts_in_its_elements():
    assert parse_molecular_formula("C11H12NO+").elements == (
        ("C", 11), ("H", 12), ("N", 1), ("O", 1)
    )


@pytest.mark.parametrize(
    ("text", "hill"),
    [
        ("H6C2", "C2H6"),     # carbon first, then hydrogen
        ("OC2H6", "C2H6O"),   # then the rest alphabetically
        ("H2O", "H2O"),       # no carbon: strictly alphabetical
        ("NaCl", "ClNa"),
    ],
)
def test_hill_order_is_canonical_whatever_the_input_order(text, hill):
    assert parse_molecular_formula(text).hill == hill


@pytest.mark.parametrize("text", ["", "C10H12(NO2)2", "c2h6", "C2H6.H2O", "C0H2"])
def test_formulas_outside_the_supported_form_are_rejected(text):
    """Groups, hydrates, lower-case symbols and zero counts are not plain element counts."""
    assert parse_molecular_formula(text) is None
