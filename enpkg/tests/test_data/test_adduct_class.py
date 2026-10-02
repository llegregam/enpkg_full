"""Unit tests for AdductRecipe math and the ChemicalAdduct computed mass."""

import numpy as np
import pytest

from enpkg.monolith.data.ms1_data_classes.adduct_class import (
    ADDUCT_MASSES,
    AdductRecipe,
    ChemicalAdduct,
)
from enpkg.monolith.enhancers.adducts import NEGATIVE_RECIPES, POSITIVE_RECIPES
from enpkg.monolith.enhancers.graph_adducts import GRAPH_NEGATIVE_RECIPES, GRAPH_POSITIVE_RECIPES

# Monoisotopic atomic masses and the electron mass (Da), independent of ADDUCT_MASSES.
_H, _N, _NA, _CA, _CL, _ELECTRON = (
    1.00782503207, 14.0030740048, 22.9897692809, 39.96259098, 34.96885268, 0.00054857990946,
)

# Ionic charge of each charged ingredient; every other ingredient is neutral.
_INGREDIENT_CHARGES = {
    "proton": 1, "sodium": 1, "potassium": 1, "magnesium": 2, "calcium": 2, "iron": 2,
    "chlorine": -1, "bromine": -1,
}


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


@pytest.mark.parametrize(
    ("ingredients", "charge", "positive", "ion_mass_added"),
    [
        ({"proton": 1}, 1, True, _H - _ELECTRON),                              # [M+H]+
        ({"sodium": 1}, 1, True, _NA - _ELECTRON),                             # [M+Na]+
        ({"proton": 1, "ammonia": 1}, 1, True, _N + 4 * _H - _ELECTRON),       # [M+NH4]+
        ({"proton": -1, "sodium": 2}, 1, True, 2 * _NA - _H - _ELECTRON),      # [M-H+2Na]+
        ({"calcium": 1}, 2, True, _CA - 2 * _ELECTRON),                        # [M+Ca]2+
        ({"chlorine": 1}, 1, False, _CL + _ELECTRON),                          # [M+Cl]-
    ],
)
def test_ion_mz_matches_exact_masses(ingredients, charge, positive, ion_mass_added):
    """The ion gains or loses electrons with its charge, so a sodium adduct adds Na+, not Na."""
    recipe = AdductRecipe(ingredients=ingredients, charge=charge, positive=positive)

    assert recipe.compute_adduct_mass(300.0) == pytest.approx(
        (300.0 + ion_mass_added) / charge, abs=1e-5
    )


@pytest.mark.parametrize(
    "recipe",
    [*POSITIVE_RECIPES, *NEGATIVE_RECIPES, *GRAPH_POSITIVE_RECIPES, *GRAPH_NEGATIVE_RECIPES],
)
def test_recipe_charge_is_carried_by_its_ingredients(recipe):
    """ADDUCT_MASSES holds ion masses, which give an exact m/z only if the recipe's
    charge is the sum of its ingredients' ionic charges."""
    carried = sum(_INGREDIENT_CHARGES.get(name, 0) * count for name, count in recipe.ingredients.items())

    assert carried == (recipe.charge if recipe.positive else -recipe.charge)


def test_compute_neutral_mass_inverts_compute_adduct_mass():
    # Round-trip: neutral -> ion m/z -> neutral must recover the starting mass,
    # for a non-trivial recipe (multimer + multi-ingredient + charge).
    recipe = AdductRecipe(
        ingredients={"proton": 1, "sodium": 2}, charge=3, positive=True, multimer_factor=2.0
    )
    ion_mz = recipe.compute_adduct_mass(180.0634)
    assert recipe.compute_neutral_mass(ion_mz) == pytest.approx(180.0634)


def test_ingredient_complexity_sums_absolute_counts():
    # A neutral loss carries a negative count; complexity uses the magnitude.
    recipe = AdductRecipe(ingredients={"proton": -1, "magnesium": 1}, charge=1, positive=True)
    assert recipe.ingredient_complexity == 2
    assert AdductRecipe(ingredients={"proton": 1}, charge=1, positive=True).ingredient_complexity == 1


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
        ChemicalAdduct(lotus=[], recipe=make_recipe(), annotation_method="precursor-mass-search")


def test_validate_lotus_rejects_mixed_formulas(make_lotus, make_recipe):
    with pytest.raises(ValueError, match="same molecular formula"):
        ChemicalAdduct(
            lotus=[
                make_lotus(structure_molecular_formula="C2H6"),
                make_lotus(structure_molecular_formula="C6H12O6"),
            ],
            recipe=make_recipe(),
            annotation_method="precursor-mass-search",
        )


def test_get_scores_return_hammer_arrays(make_adduct, make_lotus):
    adduct = make_adduct(
        lotus=[make_lotus(structure_taxonomy_hammer_pathways=np.array([0.2, 0.8]))]
    )
    np.testing.assert_array_equal(adduct.get_pathway_scores(), np.array([0.2, 0.8]))
