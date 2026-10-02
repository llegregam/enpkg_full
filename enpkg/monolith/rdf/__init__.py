"""RDF serialization of ENPKG `Analysis` objects.

See ``docs/RDF_KG_DATA_MODEL.md`` for the emitted graph and ``docs/vocab/enpkg.ttl``
for what each ``enpkg:`` term means.
"""

from .serializer import AnalysisSerializer, serialize_to_turtle

__all__ = ["AnalysisSerializer", "serialize_to_turtle"]
