"""Central registry of pipeline blocks exposed to the GUI.

Adding a new block to the GUI requires exactly one entry here: the display
label, the Pydantic config class (or None), the PipelineStep subclass, and a
list of dependency block ids. The app, form builder, and runner all read this
registry rather than hard-coding the list of steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Type

from pydantic import BaseModel

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig
from enpkg.monolith.pipeline.base_pipeline_step import PipelineStep
from enpkg.monolith.pipeline.molecular_networking_step import MolecularNetworkingStep
from enpkg.monolith.pipeline.ms1_enhancement_step import MS1EnhancementStep
from enpkg.monolith.pipeline.ms2_enhancement_step import MS2EnrichmentStep
from enpkg.monolith.pipeline.sirius_enhancement_step import SiriusEnhancementStep
from enpkg.monolith.pipeline.taxonomical_enhancement_step import TaxonomicalEnhancementStep
from enpkg.monolith.pipeline.weights_enhancement_step import WeightsEnhancementStep


@dataclass(frozen=True)
class BlockSpec:
    id: str
    label: str
    step_cls: Type[PipelineStep]
    config_cls: Optional[Type[BaseModel]]
    description: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)


# Canonical execution order — the runner iterates this list in order.
BLOCKS: list[BlockSpec] = [
    BlockSpec(
        id="taxonomical",
        label="Taxonomical enrichment",
        step_cls=TaxonomicalEnhancementStep,
        config_cls=None,
        description="Fetches Open Tree of Life matches for the source organism. Requires source_taxon in metadata.",
    ),
    BlockSpec(
        id="network",
        label="Molecular networking",
        step_cls=MolecularNetworkingStep,
        config_cls=NetworkEnhancerConfig,
        description="Builds a spectral similarity network from MS/MS spectra.",
    ),
    BlockSpec(
        id="ms1",
        label="MS1 enhancement",
        step_cls=MS1EnhancementStep,
        config_cls=MSEnhancerConfig,
        description="Matches MS1 precursor m/z against adduct libraries. Shares config with MS2.",
    ),
    BlockSpec(
        id="ms2",
        label="MS2 enhancement",
        step_cls=MS2EnrichmentStep,
        config_cls=MSEnhancerConfig,
        description="Matches MS/MS spectra against spectral databases (ISDB).",
    ),
    BlockSpec(
        id="sirius",
        label="Sirius",
        step_cls=SiriusEnhancementStep,
        config_cls=SiriusEnhancerConfig,
        description="Runs the external Sirius binary for structure identification.",
    ),
    BlockSpec(
        id="weights",
        label="Weights / reranking",
        step_cls=WeightsEnhancementStep,
        config_cls=ReweightingConfig,
        description="Reranks annotations using taxonomic and chemical consistency. Requires the molecular network.",
        depends_on=("network",),
    ),
]

BLOCKS_BY_ID: dict[str, BlockSpec] = {b.id: b for b in BLOCKS}

# Blocks that share a single MSEnhancerConfig instance in the unified YAML.
MS_SHARED_BLOCKS = ("ms1", "ms2")
MS_SHARED_KEY = "ms_enhancer"
