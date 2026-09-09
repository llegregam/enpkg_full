"""Exceptions used through the monolith package."""


class MonolithError(Exception):
    """Base class for exceptions in the monolith package."""


class PipelineError(MonolithError):
    """Exception raised for errors in the pipeline."""


class ConfigurationError(PipelineError):
    """Exception raised for errors in the configuration."""

class GraphNotSetError(PipelineError):
    """Exception raised when a graph is not set."""

    def __init__(self):
        """Initialize the exception."""
        super().__init__("The graph was not set by the enrichment pipeline")

class EnrichmentError(MonolithError):
    """Exception raised for errors in the enrichment process."""


class DatabaseError(MonolithError):
    """Exception raised for errors reaching or reading the DuckDB database.

    Covers a missing or unusable database path, an empty compounds table, an
    unregistered spectral library, and a spectral-library import that does not
    satisfy the expected column contract.
    """
