"""Molecular formulas as element counts plus a net charge.

Parses the formula strings the structure sources carry (LOTUS, spectral libraries):
a run of element symbols with optional counts, optionally followed by a net-charge
suffix, as in ``C15H20O6``, ``C11H12NO+`` or ``C13H20N2O4P+2``. Anything else (bracketed
groups, hydrate dots, isotope labels) is rejected rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_FORMULA = re.compile(r"((?:[A-Z][a-z]?\d*)+)([+-]\d*)?")
_ELEMENT = re.compile(r"([A-Z][a-z]?)(\d*)")


@dataclass(frozen=True)
class MolecularFormula:
    """A molecular formula as element counts in Hill order, plus a net charge.

    Attributes:
        elements: ``(symbol, count)`` pairs in Hill order — carbon, then hydrogen, then
            the remaining symbols alphabetically; strictly alphabetical when there is
            no carbon. Every count is at least 1.
        charge: Net charge of the molecule; 0 for a neutral one.
    """

    elements: tuple[tuple[str, int], ...]
    charge: int

    @property
    def hill(self) -> str:
        """The formula written in Hill order with its charge suffix (``C11H12NO+``).

        Two strings naming the same composition and charge give the same value, so it
        serves as the formula's identity.
        """
        body = "".join(f"{symbol}{count if count > 1 else ''}" for symbol, count in self.elements)
        if self.charge == 0:
            return body
        sign = "+" if self.charge > 0 else "-"
        magnitude = abs(self.charge)
        return f"{body}{sign}{magnitude if magnitude > 1 else ''}"


def _hill_key(symbol: str, has_carbon: bool) -> tuple[int, str]:
    if has_carbon and symbol == "C":
        return (0, symbol)
    if has_carbon and symbol == "H":
        return (1, symbol)
    return (2, symbol)


def parse_molecular_formula(text: str) -> Optional[MolecularFormula]:
    """Parse ``text`` into element counts and a net charge.

    A symbol that appears more than once has its counts summed (``CH3CH2OH`` is
    ``C2H6O``). The charge suffix is a sign followed by an optional magnitude.

    Returns:
        The parsed formula, or ``None`` when ``text`` is not of the supported form or
        gives an element a count of zero.
    """
    match = _FORMULA.fullmatch(text.strip())
    if match is None:
        return None
    body, suffix = match.groups()
    counts: dict[str, int] = {}
    for symbol, count in _ELEMENT.findall(body):
        number = int(count) if count else 1
        if number == 0:
            return None
        counts[symbol] = counts.get(symbol, 0) + number
    has_carbon = "C" in counts
    elements = tuple(sorted(counts.items(), key=lambda item: _hill_key(item[0], has_carbon)))
    charge = 0
    if suffix:
        magnitude = int(suffix[1:]) if len(suffix) > 1 else 1
        charge = magnitude if suffix[0] == "+" else -magnitude
    return MolecularFormula(elements=elements, charge=charge)
