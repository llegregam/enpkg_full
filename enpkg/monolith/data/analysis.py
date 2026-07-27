"""Analysis data model for mass spectrometry runs."""

from typing import Optional, Self, Tuple

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.data.otl_class import Match
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.exceptions import EnrichmentError


class Analysis(BaseModel):
    """
    Represents a mass spectrometry analysis run with associated metadata.

    This is an immutable data model. Use model_copy(update={...}) to create
    modified copies.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_name: str
    spectra: Tuple[AnnotatedSpectrum, ...]
    metadata: SampleMetadata
    ionization_mode: str
    ott_matches: list[Match] = Field(default_factory=list)
    molecular_network: Optional[nx.Graph] = None
    # Directed graph relating features that look like adducts of the same molecule
    # (nodes = feature ids). Attached by the MS1 graph enhancer; see
    # enpkg/monolith/utils/ms1_adduct_graph.py.
    ms1_adduct_graph: Optional[nx.DiGraph] = None

    # --- Convenience properties ---

    @property
    def sample_id(self) -> str:
        """Return the sample ID."""
        return self.metadata.sample_id

    @property
    def source_taxon(self) -> Optional[str]:
        """Return the source taxon, or None if not defined."""
        return self.metadata.source_taxon

    @property
    def has_source_taxon(self) -> bool:
        """Returns True if source_taxon is defined and valid."""
        return self.metadata.has_source_taxon()

    @property
    def genus_and_species(self) -> Tuple[str, str]:
        """
        Returns a tuple of (genus, species) from source_taxon.

        Raises:
            EnrichmentError: If source_taxon is not defined or doesn't contain a space.
        """
        if not self.has_source_taxon:
            raise EnrichmentError("Source taxon is not defined.")
        # Normalise ("Genus sp. Foo" / "Genus x species" -> "genus …") before
        # splitting so hybrid/unspecified markers don't land in genus/species.
        normalized = self.metadata.normalized_source_taxon
        if " " not in normalized:
            raise EnrichmentError(
                f"Source taxon '{self.source_taxon}' does not contain genus and species."
            )
        return tuple(normalized.split(" ", 2)[:2])

    @property
    def best_ott_matches(self) -> Optional[Match]:
        """Returns the best OTT match (first in the list) or None if no matches."""
        return self.ott_matches[0] if self.ott_matches else None

    @property
    def number_of_spectra(self) -> int:
        """Returns the number of spectra in the analysis."""
        return len(self.spectra)

    @property
    def feature_ids(self) -> list[str]:
        """
        Returns the feature IDs of all spectra.

        Raises:
            ValueError: If any spectrum is missing a feature_id.
        """
        ids = []
        for i, spectrum in enumerate(self.spectra):
            feature_id = spectrum.metadata.get("feature_id")
            if feature_id is None:
                raise ValueError(f"Spectrum at index {i} is missing 'feature_id' in metadata.")
            ids.append(feature_id)
        return ids

    # --- Validators ---

    @model_validator(mode='after')
    def validate_network_integrity(self) -> Self:
        """
        Validates that the molecular network (if set) is consistent with
        the spectra in the analysis.
        """
        if self.molecular_network is None:
            return self

        feature_ids = self.feature_ids

        # Check node count
        if len(self.molecular_network.nodes) != self.number_of_spectra:
            raise ValueError(
                f"Network has {len(self.molecular_network.nodes)} nodes, "
                f"but analysis has {self.number_of_spectra} spectra."
            )

        # Check feature IDs match
        if set(self.molecular_network.nodes) != set(feature_ids):
            raise ValueError("Network nodes do not match spectrum feature IDs.")

        # Check order
        if list(self.molecular_network.nodes) != feature_ids:
            raise ValueError("Network node order must match spectra order.")

        return self

    @model_validator(mode='after')
    def validate_adduct_graph_integrity(self) -> Self:
        """Validate the MS1 adduct graph (if set) covers exactly the analysis features.
        """
        if self.ms1_adduct_graph is None:
            return self

        if set(self.ms1_adduct_graph.nodes) != set(self.feature_ids):
            raise ValueError("Adduct graph nodes do not match spectrum feature IDs.")

        return self
