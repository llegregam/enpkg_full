"""Configuration classes for ISDB enrichment."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from enpkg.monolith.configuration.config import EnhancerConfig, GeneralParams


class Urls(BaseModel):
    """URLs for remote data files."""

    taxo_db_metadata: Optional[str] = Field(
        default="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
        description="URL for taxonomic database metadata"
    )
    spectral_db_pos: Optional[str] = Field(
        default="https://zenodo.org/records/8287341/files/isdb_pos_cleaned.pkl",
        description="URL for positive mode spectral database"
    )
    spectral_db_neg: Optional[str] = Field(
        default=None,
        description="URL for negative mode spectral database"
    )
    taxo_db_pathways: Optional[str] = Field(
        default="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
        description="URL for taxonomic pathways database"
    )
    taxo_db_superclasses: Optional[str] = Field(
        default="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
        description="URL for taxonomic superclasses database"
    )
    taxo_db_classes: Optional[str] = Field(
        default="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1",
        description="URL for taxonomic classes database"
    )

    @property
    def empty(self) -> bool:
        """Check if all URLs are None or empty strings."""
        for field_name in self.__class__.model_fields:
            value = getattr(self, field_name)
            if value is not None and value.strip():
                return False
        return True

    def __iter__(self):
        """Iterate over non-None, non-empty URL values."""
        for field_name in self.__class__.model_fields:
            value = getattr(self, field_name)
            if value is not None and value.strip():
                yield value

    def items(self):
        """Iterate over (field_name, url) pairs for non-None, non-empty URLs."""
        for field_name in self.__class__.model_fields:
            value = getattr(self, field_name)
            if value is not None and value.strip():
                yield field_name, value

    def __len__(self) -> int:
        """Return count of non-None, non-empty URLs."""
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
            for field_name in self.__class__.model_fields
            if getattr(self, field_name) is not None
        ]

    def __iter__(self):
        """Iterate over non-None path values."""
        for field_name in self.__class__.model_fields:
            value = getattr(self, field_name)
            if value is not None:
                yield value

    def items(self):
        """Iterate over (field_name, path) pairs for non-None paths."""
        for field_name in self.__class__.model_fields:
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
        description="Parent (precursor) m/z tolerance in Daltons. Governs both the "
        "MS2 precursor pre-filter and the MS1 adduct mass window. Note: this is a "
        "Dalton tolerance, unlike the legacy workflow's ppm MS1 tolerance."
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
    method: Literal["cosine_greedy", "cosine_hungarian"] = Field(
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
    paths: Optional[Paths] = Field(
        default=None,
        description="Local file paths for databases (optional, overrides download_dir)"
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

    ms2_adduct_filter: Literal["non_satellite", "base_only", "all"] = Field(
        default="non_satellite",
        description="Which features the MS2 enhancer annotates, based on the MS1 adduct "
        "graph's cluster roles (requires the 'ms1_graph' block to run first). Spectral "
        "libraries are overwhelmingly [M+H]+/[M-H]-, so matching resolved non-base adducts "
        "is redundant. 'non_satellite' (default): annotate base-ion anchors and singletons, "
        "skip resolved adducts ([M+Na]+/[M+K]+/...). 'base_only': annotate only resolved "
        "base-ion anchors. 'all': annotate every feature (legacy). Falls back to 'all' with "
        "a warning if no cluster roles are stamped. Read only by the MS2 enhancer.",
    )

