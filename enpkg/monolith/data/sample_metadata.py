"""Sample metadata model for Analysis."""

import math
from typing import Optional

from pydantic import BaseModel, field_validator


class SampleMetadata(BaseModel):
    """
    Structured metadata for an analysis sample.

    This replaces the unstructured series approach with explicit,
    validated fields.
    """

    sample_id: str
    sample_name: Optional[str] = None
    source_taxon: Optional[str] = None
    sample_type: Optional[str] = None
    source_id: Optional[str] = None
    organism_kingdom: Optional[str] = None
    organism_phylum: Optional[str] = None
    organism_class: Optional[str] = None
    organism_order: Optional[str] = None
    organism_family: Optional[str] = None
    organism_genus: Optional[str] = None

    # Collection provenance
    collection_date: Optional[str] = None
    collection_location: Optional[str] = None

    # Extraction (backs the RDF ExtractSample node's own attributes)
    extraction_method: Optional[str] = None
    extraction_solvent: Optional[str] = None

    # TODO: Externalize these fields
    sample_filename_pos: Optional[str] = None
    sample_filename_neg: Optional[str] = None
    massive_id: Optional[str] = None

    # Store any additional fields not explicitly defined
    extra_fields: dict = {}

    @field_validator("source_taxon", mode="before")
    @classmethod
    def normalize_source_taxon(cls, value):
        """Normalize empty/invalid source taxon values to None."""
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        if isinstance(value, str) and value.lower() in ("nd", "nan", ""):
            return None
        return value

    @classmethod
    def from_dict(cls, data: dict) -> "SampleMetadata":
        """
        Create SampleMetadata from a dictionary (e.g., a row from a Polars DataFrame).

        Known fields are extracted explicitly; unknown fields go to extra_fields.
        """
        known_fields = {
            "sample_id", "sample_name", "source_taxon", "sample_type", "source_id",
            "organism_kingdom", "organism_phylum", "organism_class",
            "organism_order", "organism_family", "organism_genus",
            "collection_date", "collection_location",
            "extraction_method", "extraction_solvent",
            "sample_filename_pos", "sample_filename_neg", "massive_id"
        }

        known_data = {k: v for k, v in data.items() if k in known_fields}
        extra_data = {k: v for k, v in data.items() if k not in known_fields}

        # Convert NaN values to None for known fields
        for key, value in known_data.items():
            if isinstance(value, float) and math.isnan(value):
                known_data[key] = None

        return cls(**known_data, extra_fields=extra_data)

    def has_source_taxon(self) -> bool:
        """Returns True if source_taxon is defined and valid."""
        return self.source_taxon is not None

    @property
    def normalized_source_taxon(self) -> Optional[str]:
        """Return ``source_taxon`` lowercased with hybrid/`sp.` markers stripped.

        Ported from the legacy ``Analysis.normalized_source_taxon`` so that
        genus/species extraction is robust to entries like ``"Genus sp. Foo"``
        or ``"Genus x species"``. Returns None when no source taxon is set.
        """
        if self.source_taxon is None:
            return None
        return (
            self.source_taxon.lower()
            .replace(" sp. ", " ")
            .replace(" x ", " ")
            .strip()
        )

    def is_sample(self) -> bool:
        """Whether this is a biological sample (as opposed to a blank / QC).

        An unset ``sample_type`` is treated as a sample, so datasets without the
        column are never dropped; only an explicit non-``sample`` value (e.g.
        ``"blank"``, ``"QC"``) marks the row as not a biological sample. Ported
        from the legacy ``sample_type in {"sample", "blank"}`` gate.
        """
        return self.sample_type is None or self.sample_type.strip().lower() == "sample"
