"""Configuration classes for MS1/MS2 spectral enrichment."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from enpkg.monolith.configuration.config import EnhancerConfig, GeneralParams


class SpectralMatchParams(BaseModel):
    """Parameters for spectral matching."""

    parent_mz_tol: float = Field(
        default=0.01,
        gt=0,
        description="MS2 precursor m/z tolerance in Daltons. Bounds the library "
        "candidate window: a library spectrum is a candidate when its precursor "
        "lies within this distance of the feature's."
    )
    ms1_ppm_tol: float = Field(
        default=10.0,
        gt=0,
        description="MS1 precursor mass tolerance in parts per million. Governs both "
        "the LOTUS exact-mass query window and the per-feature adduct match. Relative "
        "to the observed precursor m/z, so the absolute window widens with mass: "
        "10 ppm is 0.002 Da at m/z 200 and 0.010 Da at m/z 1000."
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

class MSEnhancerConfig(EnhancerConfig, BaseModel):
    """Configuration for MS Enhancers.

    Combines all sub-configurations for spectral matching against the registered
    spectral libraries, with taxonomic and chemical reweighting.
    """

    general_params: GeneralParams = Field(
        default_factory=GeneralParams,
        description="General processing parameters"
    )

    duckdb_path: str = Field(
        description="Path to the pre-built DuckDB database holding the LOTUS tables "
        "and the registered spectral libraries. Build it with the `import_lotus` and "
        "`import_spectral_library` scripts; the pipeline only ever reads it."
    )

    spectral_libraries: Optional[list[str]] = Field(
        default=None,
        description="Names of the registered spectral libraries to query, as listed by "
        "`import_spectral_library --list`. Leave empty to query every registered "
        "library matching the run's ionization mode."
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

