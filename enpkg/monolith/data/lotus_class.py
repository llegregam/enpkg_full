"""Data class representing the key information of a LOTUS entry."""

from dataclasses import dataclass
from typing import Any, Dict, Mapping

import numpy as np

from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.data.taxonomy import (
    MAXIMAL_TAXONOMICAL_SCORE,
    normalized_rank_similarity,
    rank_similarity,
)

__all__ = ["Lotus", "MAXIMAL_TAXONOMICAL_SCORE"]

@dataclass(slots=True)
class Lotus:
    """Data class representing the key information of a LOTUS entry.

    ``slots=True`` strips the per-instance ``__dict__`` (~30% memory cut
    over the full LOTUS list, which is materialised in memory by MS2).
    Safe because no consumer attaches dynamic attributes to Lotus instances
    after the LotusStore refactor.
    """

    structure_wikidata: str
    structure_inchikey: str
    structure_inchi: str
    structure_smiles: str
    structure_molecular_formula: str
    structure_exact_mass: float
    structure_xlogp: float
    structure_smiles_2d: str
    structure_cid: int
    structure_name_iupac: str
    structure_name_traditional: str
    structure_stereocenters_total: int
    structure_stereocenters_unspecified: int
    structure_taxonomy_hammer_pathways: np.ndarray
    structure_taxonomy_hammer_superclasses: np.ndarray
    structure_taxonomy_hammer_classes: np.ndarray
    structure_taxonomy_classyfire_chemontid: str
    structure_taxonomy_classyfire_01kingdom: str
    structure_taxonomy_classyfire_02superclass: str
    structure_taxonomy_classyfire_03class: str
    structure_taxonomy_classyfire_04directparent: str
    organism_wikidata: str
    organism_name: str
    organism_taxonomy_gbifid: int
    organism_taxonomy_ncbiid: int
    organism_taxonomy_ottid: int
    domain: str
    kingdom: str
    phylum: str
    klass: str
    order: str
    family: str
    tribe: str
    genus: str
    species: str
    varietas: str
    reference_wikidata: str
    reference_doi: str
    manual_validation: bool

    @classmethod
    def from_row(
        cls,
        columns: Mapping[str, int],
        series: list[Any],
        pathways: np.ndarray,
        superclasses: np.ndarray,
        classes: np.ndarray,
    ) -> "Lotus":
        """Create a Lotus object from a parsed row using an explicit column index map.

        The column map is passed in per-call so that multiple Lotus-producing
        stores can coexist without sharing class-level state.
        """
        series = [
            None if (value is None or (isinstance(value, float) and np.isnan(value))) else value
            for value in series
        ]

        return cls(
            structure_wikidata=series[columns["structure_wikidata"]],
            structure_inchikey=series[columns["structure_inchikey"]],
            structure_inchi=series[columns["structure_inchi"]],
            structure_smiles=series[columns["structure_smiles"]],
            structure_molecular_formula=series[columns["structure_molecular_formula"]],
            structure_exact_mass=series[columns["structure_exact_mass"]],
            structure_xlogp=series[columns["structure_xlogp"]],
            structure_smiles_2d=series[columns["structure_smiles_2D"]],
            structure_cid=series[columns["structure_cid"]],
            structure_name_iupac=series[columns["structure_nameIupac"]],
            structure_name_traditional=series[columns["structure_nameTraditional"]],
            structure_taxonomy_hammer_pathways=pathways,
            structure_taxonomy_hammer_superclasses=superclasses,
            structure_taxonomy_hammer_classes=classes,
            structure_stereocenters_total=series[columns["structure_stereocenters_total"]],
            structure_stereocenters_unspecified=series[columns["structure_stereocenters_unspecified"]],
            structure_taxonomy_classyfire_chemontid=series[columns["structure_taxonomy_classyfire_chemontid"]],
            structure_taxonomy_classyfire_01kingdom=series[columns["structure_taxonomy_classyfire_01kingdom"]],
            structure_taxonomy_classyfire_02superclass=series[columns["structure_taxonomy_classyfire_02superclass"]],
            structure_taxonomy_classyfire_03class=series[columns["structure_taxonomy_classyfire_03class"]],
            structure_taxonomy_classyfire_04directparent=series[columns["structure_taxonomy_classyfire_04directparent"]],
            organism_wikidata=series[columns["organism_wikidata"]],
            organism_name=series[columns["organism_name"]],
            organism_taxonomy_gbifid=series[columns["organism_taxonomy_gbifid"]],
            organism_taxonomy_ncbiid=series[columns["organism_taxonomy_ncbiid"]],
            organism_taxonomy_ottid=series[columns["organism_taxonomy_ottid"]],
            domain=series[columns["organism_taxonomy_01domain"]],
            kingdom=series[columns["organism_taxonomy_02kingdom"]],
            phylum=series[columns["organism_taxonomy_03phylum"]],
            klass=series[columns["organism_taxonomy_04class"]],
            order=series[columns["organism_taxonomy_05order"]],
            family=series[columns["organism_taxonomy_06family"]],
            tribe=series[columns["organism_taxonomy_07tribe"]],
            genus=series[columns["organism_taxonomy_08genus"]],
            species=series[columns["organism_taxonomy_09species"]],
            varietas=series[columns["organism_taxonomy_10varietas"]],
            reference_wikidata=series[columns["reference_wikidata"]],
            reference_doi=series[columns["reference_doi"]],
            manual_validation=series[columns["manual_validation"]],
        )

    def __hash__(self) -> int:
        """Return the hash of the InChIKey."""
        return hash((
            self.structure_inchikey,
            self.organism_taxonomy_ottid
        ))

    def __repr__(self) -> str:
        """Return a string representation of the LOTUS entry."""
        return (
            f"Lotus(\nTraditional name: {self.structure_name_traditional}, "
            f"\nInChIKey: {self.structure_inchikey},"
            f"\nOrganism: {self.organism_name}),"
            f"\nMolecular formula: {self.structure_molecular_formula},"
        )

    def __eq__(self, other: object) -> bool:
        """Return whether two LOTUS entries are equal."""
        if not isinstance(other, Lotus):
            return False

        return (
            self.structure_inchikey == other.structure_inchikey
            and self.organism_taxonomy_ottid == other.organism_taxonomy_ottid
        )

    def to_dict(self) -> Dict[str, Any]:
        """Returns the principal informations of the LOTUS entry as a dictionary.

        Implementative details
        ----------------------
        We solely maintain the features that can be represented
        in a tabular form. Complex features such as the scores are,
        therefore, excluded.
        """
        return {
            "structure_inchikey": self.structure_inchikey,
            "structure_inchi": self.structure_inchi,
            "structure_smiles": self.structure_smiles,
            "structure_molecular_formula": self.structure_molecular_formula,
            "structure_exact_mass": self.structure_exact_mass,
            "structure_xlogp": self.structure_xlogp,
            "structure_smiles_2d": self.structure_smiles_2d,
            "structure_cid": self.structure_cid,
            "structure_name_iupac": self.structure_name_iupac,
            "structure_name_traditional": self.structure_name_traditional,
            "structure_stereocenters_total": self.structure_stereocenters_total,
            "structure_stereocenters_unspecified": self.structure_stereocenters_unspecified,
            "structure_taxonomy_classyfire_chemontid": self.structure_taxonomy_classyfire_chemontid,
            "structure_taxonomy_classyfire_01kingdom": self.structure_taxonomy_classyfire_01kingdom,
            "structure_taxonomy_classyfire_02superclass": self.structure_taxonomy_classyfire_02superclass,
            "structure_taxonomy_classyfire_03class": self.structure_taxonomy_classyfire_03class,
            "structure_taxonomy_classyfire_04directparent": self.structure_taxonomy_classyfire_04directparent,
            "organism_wikidata": self.organism_wikidata,
            "organism_name": self.organism_name,
            "organism_taxonomy_gbifid": self.organism_taxonomy_gbifid,
            "organism_taxonomy_ncbiid": self.organism_taxonomy_ncbiid,
            "organism_taxonomy_ottid": self.organism_taxonomy_ottid,
            "domain": self.domain,
            "kingdom": self.kingdom,
            "phylum": self.phylum,
            "klass": self.klass,
            "order": self.order,
            "family": self.family,
            "tribe": self.tribe,
            "genus": self.genus,
            "species": self.species,
            "varietas": self.varietas,
            "reference_wikidata": self.reference_wikidata,
            "reference_doi": self.reference_doi,
            "manual_validation": self.manual_validation,
        }

    @property
    def short_inchikey(self) -> str:
        """Return the first 14 characters of the InChIKey."""
        return self.structure_inchikey[:14]


    def taxonomical_similarity_with_otl_match(self, match: Match) -> float:
        """Rank-ladder taxonomical similarity with an OTL match.

        Score is the most-specific shared taxonomic rank: 8 = species, 7 = genus,
        ... 1 = domain, 0 = nothing shared. See
        :func:`enpkg.monolith.data.taxonomy.rank_similarity`.
        """
        return rank_similarity(self, match)

    def normalized_taxonomical_similarity_with_otl_match(self, match: Match) -> float:
        """:meth:`taxonomical_similarity_with_otl_match` normalised to ``[0, 1]``."""
        return normalized_rank_similarity(self, match)
