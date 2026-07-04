"""Unit tests for AdductRecipe math and the ChemicalAdduct computed mass."""

import numpy as np
import pytest

from enpkg.monolith.data.ms1_data_classes.adduct_class import (
    ADDUCT_MASSES,
    AdductRecipe,
    ChemicalAdduct,
)


def test_protonation_mass():
    # [M+H]+ : (M + proton) / 1
    recipe = AdductRecipe(ingredients={"proton": 1}, charge=1, positive=True)
    assert recipe.compute_adduct_mass(100.0) == pytest.approx(100.0 + ADDUCT_MASSES["proton"])


def test_multimer_and_charge_are_applied():
    # [2M+2H]2+ : (2*M + 2*proton) / 2
    recipe = AdductRecipe(
        ingredients={"proton": 2}, charge=2, positive=True, multimer_factor=2.0
    )
    expected = (2 * 100.0 + 2 * ADDUCT_MASSES["proton"]) / 2
    assert recipe.compute_adduct_mass(100.0) == pytest.approx(expected)


def test_multiple_ingredients_sum():
    recipe = AdductRecipe(ingredients={"sodium": 1, "water": 1}, charge=1, positive=True)
    expected = 100.0 + ADDUCT_MASSES["sodium"] + ADDUCT_MASSES["water"]
    assert recipe.compute_adduct_mass(100.0) == pytest.approx(expected)


def test_chemical_adduct_mass_is_derived_from_recipe(make_adduct, make_lotus, make_recipe):
    lotus = make_lotus(structure_exact_mass=180.0634)
    adduct = make_adduct(lotus=[lotus], recipe=make_recipe(ingredients={"proton": 1}, charge=1))
    assert adduct.adduct_mass == pytest.approx(180.0634 + ADDUCT_MASSES["proton"])
    # Derived property, not a stored field:
    assert "adduct_mass" not in ChemicalAdduct.model_fields


def test_chemical_adduct_convenience_properties(make_adduct, make_lotus):
    adduct = make_adduct(lotus=[make_lotus(structure_molecular_formula="C6H12O6")])
    assert adduct.molecular_formula == "C6H12O6"
    assert adduct.positive is True
    assert len(adduct.short_inchikey) == 14


def test_validate_lotus_rejects_empty(make_recipe):
    with pytest.raises(ValueError, match="must not be empty"):
        ChemicalAdduct(lotus=[], recipe=make_recipe())


def test_validate_lotus_rejects_mixed_formulas(make_lotus, make_recipe):
    with pytest.raises(ValueError, match="same molecular formula"):
        ChemicalAdduct(
            lotus=[
                make_lotus(structure_molecular_formula="C2H6"),
                make_lotus(structure_molecular_formula="C6H12O6"),
            ],
            recipe=make_recipe(),
        )


def test_get_scores_return_hammer_arrays(make_adduct, make_lotus):
    adduct = make_adduct(
        lotus=[make_lotus(structure_taxonomy_hammer_pathways=np.array([0.2, 0.8]))]
    )
    np.testing.assert_array_equal(adduct.get_pathway_scores(), np.array([0.2, 0.8]))
