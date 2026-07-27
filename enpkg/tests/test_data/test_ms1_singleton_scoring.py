"""Unit tests for the pure singleton MS1 adduct-candidate scorers."""

import pytest

from enpkg.monolith.data.ms1_data_classes.adduct_class import AdductRecipe
from enpkg.monolith.utils.ms1_singleton_scoring import (
    adduct_plausibility_score,
    mass_accuracy_score,
    singleton_candidate_score,
)

# Default exponents mirror MS1GraphEnhancerConfig's proposed starting values.
_CE = 2.0  # charge_exponent
_XE = 1.0  # complexity_exponent


def test_mass_accuracy_is_one_at_zero_error():
    assert mass_accuracy_score(0.0, sigma_da=0.005) == pytest.approx(1.0)


def test_mass_accuracy_decays_with_larger_error():
    tight = mass_accuracy_score(0.002, sigma_da=0.005)
    loose = mass_accuracy_score(0.010, sigma_da=0.005)
    assert 0.0 < loose < tight < 1.0


def test_mass_accuracy_is_symmetric_in_sign():
    assert mass_accuracy_score(0.003, sigma_da=0.005) == pytest.approx(
        mass_accuracy_score(-0.003, sigma_da=0.005)
    )


def test_mass_accuracy_rejects_nonpositive_sigma():
    with pytest.raises(ValueError, match="sigma_da must be > 0"):
        mass_accuracy_score(0.0, sigma_da=0.0)


def test_plausibility_is_one_for_protonated():
    recipe = AdductRecipe(ingredients={"proton": 1}, charge=1, positive=True)
    assert adduct_plausibility_score(
        recipe.charge, recipe.ingredient_complexity, _CE, _XE
    ) == pytest.approx(1.0)


def test_plausibility_orders_the_issue_doc_candidates():
    # The five competing candidates from docs/MS1_ADDUCT_RANKING_ISSUE.md.
    recipes = {
        "[M+H]+": AdductRecipe(ingredients={"proton": 1}, charge=1, positive=True),
        "[M+Mg]2+": AdductRecipe(ingredients={"magnesium": 1}, charge=2, positive=True),
        "[M+Ca]2+": AdductRecipe(ingredients={"calcium": 1}, charge=2, positive=True),
        "[M+H+Na]2+": AdductRecipe(ingredients={"proton": 1, "sodium": 1}, charge=2, positive=True),
        "[M+H+2Na]3+": AdductRecipe(ingredients={"proton": 1, "sodium": 2}, charge=3, positive=True),
    }
    score = {
        name: adduct_plausibility_score(r.charge, r.ingredient_complexity, _CE, _XE)
        for name, r in recipes.items()
    }
    # [M+H]+ > [M+Mg]2+ == [M+Ca]2+ > [M+H+Na]2+ > [M+H+2Na]3+
    assert score["[M+H]+"] > score["[M+Mg]2+"]
    assert score["[M+Mg]2+"] == pytest.approx(score["[M+Ca]2+"])
    assert score["[M+Mg]2+"] > score["[M+H+Na]2+"] > score["[M+H+2Na]3+"]


def test_singleton_score_combines_both_factors():
    # A mass-perfect [M+H]+ must outrank an equally mass-perfect [M+H+2Na]3+.
    mh = singleton_candidate_score(
        0.0, charge=1, ingredient_complexity=1, sigma_da=0.005, charge_exponent=_CE, complexity_exponent=_XE
    )
    exotic = singleton_candidate_score(
        0.0, charge=3, ingredient_complexity=3, sigma_da=0.005, charge_exponent=_CE, complexity_exponent=_XE
    )
    assert mh == pytest.approx(1.0)
    assert mh > exotic


def test_singleton_score_penalises_mass_error():
    perfect = singleton_candidate_score(
        0.0, charge=1, ingredient_complexity=1, sigma_da=0.005, charge_exponent=_CE, complexity_exponent=_XE
    )
    drifted = singleton_candidate_score(
        0.008, charge=1, ingredient_complexity=1, sigma_da=0.005, charge_exponent=_CE, complexity_exponent=_XE
    )
    assert perfect > drifted
