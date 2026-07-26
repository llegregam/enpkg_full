"""Submodule providing the data class for representing chemical adducts."""

from dataclasses import dataclass
from typing import Dict

import numpy as np
from pydantic import BaseModel, ConfigDict, field_validator

from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.otl_class import Match

ADDUCT_MASSES: Dict[str, float] = {
    "proton": 1.00728,
    "ammonium": 17.02655,
    "water": 18.01056,
    "sodium": 22.98977,
    "magnesium": 23.98504,
    "methanol": 32.02621,
    "chlorine": 34.96885,
    "potassium": 38.96371,
    "calcium": 39.96259,
    "acetonitrile": 41.02655,
    "ethylamine": 45.05785,
    "formic": 46.00548,
    "iron": 55.93494,
    "acetic": 60.02113,
    "isopropanol": 60.05751,
    "dmso": 78.01394,
    "bromine": 78.91834,
    "tfa": 113.99286,
}


@dataclass
class AdductRecipe:
    """Dataclass representing an adduct recipe.

    Attributes:
        ingredients: Dict[str, int] - A dictionary of adducts and their counts.
        charge: int - The charge of the adduct.
        multimer_factor: int - The factor by which the adduct mass should be multiplied.
        positive: bool - Whether the adduct is positive or negative.
    """

    ingredients: Dict[str, float]
    charge: float
    positive: bool
    multimer_factor: float = 1.0

    def compute_adduct_mass(self, exact_lotus_mass: float) -> float:
        """Applies the adduct recipe to the provided exact lotus mass"""
        return (
            self.multimer_factor * exact_lotus_mass
            + sum(ADDUCT_MASSES[key] * count for key, count in self.ingredients.items())
        ) / self.charge

    def compute_neutral_mass(self, observed_mz: float) -> float:
        """Inverse of ``compute_adduct_mass``: the neutral (monomer) mass that, under
        this recipe, would appear at ``observed_mz``.

        Used by the MS1 adduct-relationship graph to test whether one peak seen as
        recipe A implies another peak seen as recipe B (both from the same molecule).
        """
        ingredient_sum = sum(
            ADDUCT_MASSES[key] * count for key, count in self.ingredients.items()
        )
        return (observed_mz * self.charge - ingredient_sum) / self.multimer_factor

    @property
    def ingredient_complexity(self) -> float:
        """Total ionization complexity: the sum of absolute ingredient counts.

        Feeds the singleton adduct-plausibility prior: ``[M+H]+`` (one proton) scores
        1, whereas ``[M+H+2Na]3+`` (``{proton: 1, sodium: 2}``) scores 3.
        """
        return sum(abs(count) for count in self.ingredients.values())


class ChemicalAdduct(BaseModel):
    """Data class representing a chemical adduct.

    Attributes:
    -----------
    lotus: list[Lotus]
        A list of Lotus entries that are part of the adduct.
        All Lotus entries must have the same exact mass and chemical formula.
    recipe: AdductRecipe
        The recipe used to create the adduct.
    adduct_mass: float
        The mass of the adduct determined using the recipe and the exact mass of the Lotus entries.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True) # For handling np.ndarrays. Will be removed once Lotus class is refactored
    lotus: list[Lotus]
    recipe: AdductRecipe

    @field_validator("lotus", mode="after")
    @classmethod
    def validate_lotus(cls, lotus:list[Lotus]) -> list[Lotus]:
        """"Validate that the list of lotus entries is not empty"""
        if len(lotus) == 0:
            raise ValueError("The lotus must not be empty")

        # All lotus entries must have the same exact mass and chemical formula
        structure_molecular_formula = lotus[0].structure_molecular_formula

        wrong_mass_entries = [
            lotus_entry for lotus_entry in lotus[1:]
            if lotus_entry.structure_molecular_formula != structure_molecular_formula
        ]
        if wrong_mass_entries:
            raise ValueError(
                f"All lotus entries must have the same molecular formula, "
                f"but got {structure_molecular_formula} and {[entry.structure_molecular_formula for entry in wrong_mass_entries]}"
            )
        return lotus

    @property
    def adduct_mass(self) -> float:
        """Ion mass from applying the recipe to the group's exact mass.

        Every Lotus entry in the group shares a molecular formula (enforced by
        ``validate_lotus``), so ``lotus[0]``'s exact mass is representative.
        """
        return self.recipe.compute_adduct_mass(self.lotus[0].structure_exact_mass)

    @property
    def short_inchikey(self) -> str:
        """Return the first 14 characters of the inchikey."""
        return self.lotus[0].short_inchikey

    @property
    def molecular_formula(self) -> str:
        """Return the molecular formula of the adduct."""
        return self.lotus[0].structure_molecular_formula

    @property
    def positive(self) -> bool:
        """Return whether the adduct is positive or negative."""
        return self.recipe.positive

    def maximal_normalized_taxonomical_similarity(self, match: Match) -> float:
        """Return the maximal normalized taxonomical similarity of the adduct."""
        return max(
            lotus.normalized_taxonomical_similarity_with_otl_match(match)
            for lotus in self.lotus
        )

    def get_pathway_scores(self) -> np.ndarray:
        """Return the NPC pathway scores for the adduct."""
        return self.lotus[0].structure_taxonomy_hammer_pathways

    def get_superclass_scores(self) -> np.ndarray:
        """Return the NPC superclass scores for the adduct."""
        return self.lotus[0].structure_taxonomy_hammer_superclasses

    def get_class_scores(self) -> np.ndarray:
        """Return the NPC class scores for the adduct."""
        return self.lotus[0].structure_taxonomy_hammer_classes
