"""Submodule defining the enhancer interface."""

from abc import ABC, abstractmethod

from enpkg.monolith.data.analysis import Analysis


class Enhancer(ABC):
    """Interface for enhancers."""

    @abstractmethod
    def enhance(self, analysis: Analysis) -> Analysis:
        """Returns the enriched analysis."""
        pass

    @abstractmethod
    def name(self) -> str:
        """Returns the name of the enhancer."""
        pass

