"""Module to store a chemical annotation."""

from abc import abstractmethod, ABC
from typing import Optional
from pydantic import BaseModel, Field

from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.otl_class import Match

class ChemicalAnnotation(BaseModel):
    """Class to store a chemical annotation."""

    # TODO: See for validating Series objects using pandera
    model_config = {"arbitrary_types_allowed": True}

    source: str = Field(
        description="The source of the chemical annotation (e.g., ISDB, MassBank, etc.)"
    )

    # The LOTUS annotations are optional because not all chemical annotations will have
    # associated LOTUS entries, for example if the annotation is based on a database
    # that does not have associated LOTUS entries or if the annotation is based on
    # a spectral match that does not have associated LOTUS entries.
    # @abstractmethod
    # def lotus_annotations(self) -> Optional[list[Lotus]]:
    #     """Return the LOTUS annotations for the annotation."""

    # def has_lotus_entries(self) -> bool:
    #     """Return whether the annotation has Lotus entries."""
    #     return self.lotus_annotations() is not None

    # def maximal_normalized_taxonomical_similarity(
    #     self, match: Match
    # ) -> Optional[float]:
    #     """Return the maximal normalized taxonomical similarity of the adduct."""
    #     if not self.has_lotus_entries():
    #         return None

    #     return max(
    #         lotus.normalized_taxonomical_similarity_with_otl_match(match)
    #         for lotus in self.lotus_annotations()
    #     )



class MS2ChemicalAnnotation(ChemicalAnnotation, BaseModel):
    """Class to store a chemical annotation for MS2 data."""
    
    scores: Optional[dict[str, dict[str, float]]] = Field(
        default=None,
        description="A dictionary of scores associated with the annotation (e.g., cosine similarity, number of matched peaks, etc.)"
    )
    queried_against: Optional[str] = Field(
        default=None,
        description="The database or spectral library against which the annotation was queried (e.g., ISDB, MassBank, etc.)"
    )
    lotus_entries: Optional[list[Lotus]] = Field(
        default=None,
        description="A list of LOTUS entries associated with the annotation, if any."
    )


