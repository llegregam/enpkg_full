"""RDF serialization of ENPKG `Analysis` objects.

See ``docs/RDF_DATA_MODEL_mapped.md`` for the entity -> vocabulary mapping.
"""

from .serializer import AnalysisSerializer, serialize_to_turtle

__all__ = ["AnalysisSerializer", "serialize_to_turtle"]