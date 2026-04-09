"""Configuration classes for ISDB enrichment."""

from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field

from enpkg.monolith.configuration.config import GeneralParams, EnhancerConfig
    

class Urls(BaseModel):
    """URLs for remote data files."""

    taxo_db_metadata: Optional[str] = Field(
        default=None,
        description="URL for taxonomic database metadata"
    )
    spectral_db_pos: Optional[str] = Field(
        default=None,
        description="URL for positive mode spectral database"
    )
    spectral_db_neg: Optional[str] = Field(
        default=None,
        description="URL for negative mode spectral database"
    )
    taxo_db_pathways: Optional[str] = Field(
        default=None,
        description="URL for taxonomic pathways database"
    )
    taxo_db_superclasses: Optional[str] = Field(
        default=None,
        description="URL for taxonomic superclasses database"
    )
    taxo_db_classes: Optional[str] = Field(
        default=None,
        description="URL for taxonomic classes database"
    )

    @property
    def empty(self) -> bool:
        """Check if all URLs are None."""
        return all(
            getattr(self, field_name) is None
            for field_name in self.model_fields
        )

    def __iter__(self):
        """Iterate over non-None URL values."""
        for field_name in self.model_fields:
            value = getattr(self, field_name)
            if value is not None:
                yield value

    def items(self):
        """Iterate over (field_name, url) pairs for non-None URLs."""
        for field_name in self.model_fields:
            value = getattr(self, field_name)
            if value is not None:
                yield field_name, value

    def __len__(self) -> int:
        """Return count of non-None URLs."""
        return sum(1 for _ in self)


class Paths(BaseModel):
    """Local paths to data files."""

    taxo_db_metadata: Optional[str] = Field(
        default=None,
        description="Local path for taxonomic database metadata"
    )
    spectral_db_pos: Optional[str]  = Field(
        default=None,
        description="Local path for positive mode spectral database"
    )
    spectral_db_neg: Optional[str] = Field(
        default=None,
        description="Local path for negative mode spectral database"
    )
    taxo_db_pathways: Optional[str] = Field(
        default=None,
        description="Local path for taxonomic pathways database"
    )
    taxo_db_superclasses: Optional[str] = Field(
        default=None,
        description="Local path for taxonomic superclasses database"
    )
    taxo_db_classes: Optional[str] = Field(
        default=None,
        description="Local path for taxonomic classes database"
    )


    def path_list(self) -> list[str]:
        """Return list of all defined paths."""
        return [
            getattr(self, field_name)
            for field_name in self.model_fields
            if getattr(self, field_name) is not None
        ]

    def __iter__(self):
        """Iterate over non-None path values."""
        for field_name in self.model_fields:
            value = getattr(self, field_name)
            if value is not None:
                yield value

    def items(self):
        """Iterate over (field_name, path) pairs for non-None paths."""
        for field_name in self.model_fields:
            value = getattr(self, field_name)
            if value is not None:
                yield field_name, value

    def __len__(self) -> int:
        """Return count of non-None paths."""
        return sum(1 for _ in self)

class SpectralMatchParams(BaseModel):
    """Parameters for spectral matching."""

    parent_mz_tol: float = Field(
        default=0.01,
        gt=0,
        description="Parent mass tolerance for spectral matching (in Da)"
    )
    msms_mz_tol: float = Field(
        default=0.01,
        gt=0,
        description="MS/MS fragment mass tolerance (in Da)"
    )
    min_score: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Minimum spectral similarity score for a match"
    )
    min_peaks: int = Field(
        default=6,
        ge=1,
        description="Minimum number of matching peaks required"
    )
    method: str = Field(
        default="cosine_greedy",
        description="Spectral similarity method to use ('cosine_greedy' or 'cosine_hungarian')"
    )

class DownloaderParams(BaseModel):
    """Parameters for controlling database downloading behavior."""

    redownload_if_exists: list | bool = Field(
        default=False,
        description="Whether to redownload database files if they already exist locally"
    )
    download_dir: Optional[str] = Field(
        default=None,
        description="Directory for downloaded files. When set, paths are auto-derived from URLs."
    )
    paths: Paths = Field(
        ...,
        description="Local file paths for databases"
    )
    urls: Optional[Urls] = Field(
        default=None,
        description="Remote URLs for database downloads (optional)"
    )
    duckdb_path: Optional[str] = Field(
        default=None,
        description="Path to persistent DuckDB file. If None, falls back to CSV/pickle loading."
    )
    
class MSEnhancerConfig(EnhancerConfig, BaseModel):
    """Configuration for MS Enhancers.
    
    Combines all sub-configurations for spectral matching against
    the In-Silico DataBase with taxonomic and chemical reweighting.
    """

    general_params: GeneralParams = Field(
        default_factory=GeneralParams,
        description="General processing parameters"
    )
    downloader_params: DownloaderParams = Field(
        default_factory=DownloaderParams,
        description="Parameters for controlling database downloading behavior"
    )
    
    spectral_match_params: SpectralMatchParams = Field(
        default_factory=SpectralMatchParams,
        description="Parameters for spectral matching"
    )
    
