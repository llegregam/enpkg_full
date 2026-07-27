"""Minimal adduct/neutral-loss recipe sets for the MS1 relationship graph.

Deliberately *small* — mzAdan's core design principle is that a compact
annotation set avoids random mass coincidences between unrelated features. This
is separate from the large ``POSITIVE_RECIPES``/``NEGATIVE_RECIPES`` used for
LOTUS candidate matching in ``adducts.py``: here we only need the handful of
transitions that connect the ionization products of a *single* molecule (base
ion, common adducts, small neutral losses, low-order multimers).

The first entry in each list is the **base** form (``[M+H]+`` / ``[M-H]-``), the
identity every graph cluster is trying to pin down. Multiply-charged ions are
intentionally excluded, matching mzAdan's default annotation set.
"""

from enpkg.monolith.data.ms1_data_classes.adduct_class import AdductRecipe

GRAPH_POSITIVE_RECIPES: list[AdductRecipe] = [
    AdductRecipe(ingredients={"proton": 1}, charge=1, positive=True),  # [M+H]+  (base)
    AdductRecipe(ingredients={"proton": 1, "ammonium": 1}, charge=1, positive=True),  # [M+NH4]+
    AdductRecipe(ingredients={"sodium": 1}, charge=1, positive=True),  # [M+Na]+
    AdductRecipe(ingredients={"potassium": 1}, charge=1, positive=True),  # [M+K]+
    AdductRecipe(ingredients={"proton": 1, "water": -1}, charge=1, positive=True),  # [M+H-H2O]+
    AdductRecipe(ingredients={"proton": 1, "ammonium": -1}, charge=1, positive=True),  # [M+H-NH3]+
    AdductRecipe(ingredients={"proton": 1}, charge=1, multimer_factor=2, positive=True),  # [2M+H]+
    AdductRecipe(ingredients={"proton": 1}, charge=1, multimer_factor=3, positive=True),  # [3M+H]+
]

GRAPH_NEGATIVE_RECIPES: list[AdductRecipe] = [
    AdductRecipe(ingredients={"proton": -1}, charge=1, positive=False),  # [M-H]-  (base)
    AdductRecipe(ingredients={"chlorine": 1}, charge=1, positive=False),  # [M+Cl]-
    AdductRecipe(ingredients={"proton": -1, "formic": 1}, charge=1, positive=False),  # [M+FA-H]-
    AdductRecipe(ingredients={"proton": -1}, charge=1, multimer_factor=2, positive=False),  # [2M-H]-
    AdductRecipe(ingredients={"proton": -1}, charge=1, multimer_factor=3, positive=False),  # [3M-H]-
]

# The base ion is the first entry by construction; expose it explicitly so the
# graph's cluster-ranking step doesn't have to hard-code recipe internals.
GRAPH_POSITIVE_BASE_RECIPE: AdductRecipe = GRAPH_POSITIVE_RECIPES[0]
GRAPH_NEGATIVE_BASE_RECIPE: AdductRecipe = GRAPH_NEGATIVE_RECIPES[0]


def graph_recipes_for_polarity(polarity: str) -> tuple[list[AdductRecipe], AdductRecipe]:
    """Return ``(recipes, base_recipe)`` for ``"pos"`` or ``"neg"``.

    Raises
    ------
    ValueError
        If ``polarity`` is neither ``"pos"`` nor ``"neg"``.
    """
    match polarity:
        case "pos":
            return GRAPH_POSITIVE_RECIPES, GRAPH_POSITIVE_BASE_RECIPE
        case "neg":
            return GRAPH_NEGATIVE_RECIPES, GRAPH_NEGATIVE_BASE_RECIPE
        case _:
            raise ValueError(f"Invalid polarity {polarity!r}, expected 'pos' or 'neg'")
