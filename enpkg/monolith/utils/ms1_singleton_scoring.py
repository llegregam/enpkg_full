"""Pure scoring functions for ranking *singleton* MS1 adduct candidates.

Features whose adduct identity the MS1 relationship graph could not resolve
(singletons and graph residuals) still carry many mass-coincident candidate
adducts. These functions order those candidates using **no** taxonomy or
chemistry-class signal — only mass accuracy and a formula-derived adduct
plausibility prior — which keeps the whole MS1 channel taxonomy-free. See
``docs/MS1_GRAPH_ENHANCER.md``.

All functions are pure (primitives in, float out) and take their tunables as
explicit arguments; defaults live on ``MS1GraphEnhancerConfig``, never here.
"""

import math


def mass_accuracy_score(mass_error_da: float, sigma_da: float) -> float:
    """Gaussian decay of a signed mass error (in Da).

    ``exp(-0.5 * (mass_error_da / sigma_da) ** 2)`` — 1.0 at zero error, decaying
    smoothly and symmetrically as the observed ion mass drifts from the candidate's
    theoretical mass. Unlike a hard tolerance window this rewards a near-perfect
    match over a barely-in-window one.

    Parameters
    ----------
    mass_error_da:
        Signed error (observed minus theoretical), in Daltons.
    sigma_da:
        Gaussian width, in Daltons. Must be > 0.
    """
    if sigma_da <= 0:
        raise ValueError(f"sigma_da must be > 0, got {sigma_da}")
    return math.exp(-0.5 * (mass_error_da / sigma_da) ** 2)


def adduct_plausibility_score(
    charge: float,
    ingredient_complexity: float,
    charge_exponent: float,
    complexity_exponent: float,
) -> float:
    """Formula-derived plausibility prior for an adduct recipe.

    ``1 / (charge ** charge_exponent * ingredient_complexity ** complexity_exponent)``
    — ``[M+H]+`` (charge 1, complexity 1) scores 1.0, while multiply-charged and
    multi-ingredient recipes score progressively lower. There is no hand-tuned
    per-recipe table: plausibility is derived purely from the recipe's charge and
    its total ingredient count (see ``AdductRecipe.ingredient_complexity``).

    Parameters
    ----------
    charge:
        Recipe charge magnitude (1, 2, 3, ...).
    ingredient_complexity:
        Sum of absolute ingredient counts.
    charge_exponent, complexity_exponent:
        How steeply to penalise charge and ingredient count, respectively.
    """
    # Clamp to >= 1 so an unusual empty/zero recipe can never divide by zero
    # or score *above* the [M+H]+ reference of 1.0.
    safe_charge = max(abs(charge), 1.0)
    safe_complexity = max(ingredient_complexity, 1.0)
    return 1.0 / (safe_charge**charge_exponent * safe_complexity**complexity_exponent)


def singleton_candidate_score(
    mass_error_da: float,
    charge: float,
    ingredient_complexity: float,
    *,
    sigma_da: float,
    charge_exponent: float,
    complexity_exponent: float,
) -> float:
    """Combined singleton ranking score: mass-accuracy × adduct-plausibility.

    The product means a candidate must be *both* mass-accurate *and* a plausible
    adduct to rank highly; a near-zero on either factor suppresses it.
    """
    return mass_accuracy_score(mass_error_da, sigma_da) * adduct_plausibility_score(
        charge, ingredient_complexity, charge_exponent, complexity_exponent
    )
