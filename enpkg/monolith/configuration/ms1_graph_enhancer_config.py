"""Configuration for the MS1 adduct-relationship graph enhancer."""

from pydantic import Field

from enpkg.monolith.configuration.config import EnhancerConfig


class MS1GraphEnhancerConfig(EnhancerConfig):
    """Parameters for building and resolving the MS1 adduct-relationship graph.

    Polarity is taken from ``general_params.polarity`` (inherited), matching the
    MS1 enhancer. The mass tolerance mirrors the MS1 enhancer's ``parent_mz_tol``
    default and should be kept in step with it.

    Retention-time gating is **always applied** (there is no toggle to disable it):
    relating a whole run's features by mass alone fuses ~97% of them into a single
    giant component and resolves almost nothing (see
    ``docs/MS1_GRAPH_SCALING_ANALYSIS.md``). ``rt_tolerance_min`` carries a sensible
    default but should be tuned to the chromatography (peak width).
    """

    mz_tolerance: float = Field(
        default=0.01,
        gt=0,
        description="m/z tolerance (Da) for adduct-relationship edges. Keep in step "
        "with the MS1 enhancer's spectral_match_params.parent_mz_tol.",
    )
    rt_tolerance_min: float = Field(
        default=0.05,
        gt=0,
        description="Retention-time tolerance (minutes): two features are related only "
        "if they co-elute within this window. Always applied — mass-only relating over "
        "a whole run fuses unrelated peaks into one component. Chromatography-dependent, "
        "roughly 0.02-0.05 min for a typical ~8-minute gradient; tune to the peak width. "
        "See MS1_GRAPH_SCALING_ANALYSIS.md.",
    )
