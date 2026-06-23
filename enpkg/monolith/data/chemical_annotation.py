"""Module to store a chemical annotation."""

from dataclasses import dataclass
from typing import Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from enpkg.monolith.data.otl_class import Match

MAXIMAL_TAXONOMICAL_SCORE: float = 8.0


@dataclass(slots=True)
class AnnotationOrganism:
    """Minimal source-organism record carried by an MS2 annotation.

    Holds only what taxonomical reranking and KG provenance need — the organism
    identity plus its lineage ranks — instead of a full ``Lotus`` per source
    organism. ``slots=True`` keeps it light since many are materialised.
    """

    name: str
    wikidata: Optional[str]
    ott_id: Optional[int]
    domain: Optional[str]
    kingdom: Optional[str]
    phylum: Optional[str]
    klass: Optional[str]
    order: Optional[str]
    family: Optional[str]
    genus: Optional[str]
    species: Optional[str]

    def taxonomical_similarity_with_match(self, match: Match) -> float:
        """Rank-ladder similarity against an OTT match.

        8 = same species, 7 = genus, ... 1 = domain, 0 = no shared rank. Mirrors
        ``Lotus.taxonomical_similarity_with_otl_match`` so reranking is unchanged.
        """
        if self.species == match.species:
            return 8.0
        if self.genus == match.genus:
            return 7.0
        if self.family == match.family:
            return 6.0
        if self.order == match.order:
            return 5.0
        if self.klass == match.klass:
            return 4.0
        if self.phylum == match.phylum:
            return 3.0
        if self.kingdom == match.kingdom:
            return 2.0
        if self.domain == match.domain:
            return 1.0
        return 0.0

    def normalized_taxonomical_similarity_with_match(self, match: Match) -> float:
        """Taxonomical similarity normalised to [0, 1]."""
        return self.taxonomical_similarity_with_match(match) / MAXIMAL_TAXONOMICAL_SCORE


class ChemicalAnnotation(BaseModel):
    """Class to store a chemical annotation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str = Field(
        description="The source of the chemical annotation (e.g., ISDB, MassBank, etc.)"
    )


class MS2ChemicalAnnotation(ChemicalAnnotation):
    """Chemical annotation for an MS2 spectral-library match.

    Holds only what downstream needs rather than the full matched ``Lotus``
    objects: the matched structure (short InChIKey), the spectral match score,
    the structure's NPC classification arrays (for chemical-class reweighting),
    and the list of source organisms (for taxonomical reranking).
    """

    short_inchikey: str = Field(
        description="Short (14-char) InChIKey of the matched structure."
    )
    score: float = Field(
        description="Spectral match score (e.g. cosine similarity) between the "
        "analysis spectrum and the matched library spectrum."
    )
    n_matched_peaks: Optional[int] = Field(
        default=None, description="Number of matched peaks in the spectral match."
    )
    queried_against: Optional[str] = Field(
        default=None, description="The spectral library queried against (e.g. ISDB)."
    )
    pathway_scores: np.ndarray = Field(
        description="NPC pathway classification scores of the matched structure."
    )
    superclass_scores: np.ndarray = Field(
        description="NPC superclass classification scores of the matched structure."
    )
    class_scores: np.ndarray = Field(
        description="NPC class classification scores of the matched structure."
    )
    organisms: list[AnnotationOrganism] = Field(
        default_factory=list,
        description="Source organisms for the matched structure (taxonomical DB).",
    )

    def has_organisms(self) -> bool:
        """Return whether the annotation has any associated source organism."""
        return len(self.organisms) > 0

    def maximal_normalized_taxonomical_similarity(
        self, match: Match
    ) -> Optional[float]:
        """Max normalised taxonomical similarity over all source organisms."""
        if not self.has_organisms():
            return None
        return max(
            organism.normalized_taxonomical_similarity_with_match(match)
            for organism in self.organisms
        )

    def get_pathway_scores(self) -> np.ndarray:
        """Return the matched structure's NPC pathway scores."""
        return self.pathway_scores

    def get_superclass_scores(self) -> np.ndarray:
        """Return the matched structure's NPC superclass scores."""
        return self.superclass_scores

    def get_class_scores(self) -> np.ndarray:
        """Return the matched structure's NPC class scores."""
        return self.class_scores
