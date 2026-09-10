"""Configuration for turning a finished :class:`Analysis` into an RDF graph.

The knobs here decide two things: how much of an annotation ranking reaches the graph,
and which optional node families are emitted at all. Both matter because the graph is
consumed by triple stores where size has a direct cost — ``include_ions`` alone adds a
node per retained peak per spectrum.

Field names match :class:`~enpkg.monolith.rdf.serializer.AnalysisSerializer`'s keyword
arguments, so ``AnalysisSerializer(**config.model_dump())`` is the whole mapping and there
is no translation layer to keep in step.
"""
from typing import Optional

from pydantic import BaseModel, Field


class SerializerConfig(BaseModel):
    """Options controlling what an ``Analysis`` emits when serialized to RDF."""

    top_k_ms1: Optional[int] = Field(
        default=5,
        description=(
            "Keep only the best k MS1 adduct annotations per spectrum. "
            "A non-positive value disables the cap and emits all of them."
        ),
    )

    top_k_ms2: Optional[int] = Field(
        default=5,
        description=(
            "Keep only the best k MS2 spectral matches per spectrum. "
            "A non-positive value disables the cap and emits all of them."
        ),
    )

    top_k_sirius: Optional[int] = Field(
        default=None,
        description=(
            "Keep only the best k SIRIUS candidates per spectrum. Defaults to no cap: "
            "candidates arrive pre-ranked and the summary file is already SIRIUS's own "
            "chosen top-X. A non-positive value also means no cap."
        ),
    )

    include_fbmn_components: bool = Field(
        default=True,
        description=(
            "Emit emi:FBMNComponent nodes. On by default: components are linear in the "
            "number of features, unlike the edges they derive from, and are the unit "
            "FBMN consumers reason about. A no-op when there is no network."
        ),
    )

    include_ions: bool = Field(
        default=False,
        description=(
            "Emit one node per retained peak of every spectrum. Off by default because "
            "this is the largest single contributor to graph size."
        ),
    )

    include_adduct_clusters: bool = Field(
        default=True,
        description="Emit enpkg:AdductCluster nodes grouping features of one compound.",
    )

    min_relative_intensity: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Drop peaks below this fraction of the spectrum's base peak before emitting "
            "ions. 0 keeps every peak. Only has an effect when include_ions is on."
        ),
    )

    max_ions_per_spectrum: Optional[int] = Field(
        default=None,
        gt=0,
        description=(
            "Keep at most this many peaks per spectrum, the most intense first. "
            "Leave unset for no cap. Only has an effect when include_ions is on."
        ),
    )
