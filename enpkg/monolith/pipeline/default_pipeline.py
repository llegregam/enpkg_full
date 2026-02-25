"""Submodule providing the default pipeline for ENPKG analysis."""

from time import time
from typing import Type, Optional
import yaml
from monolith.data import (
    ISDBEnhancerConfig,
    NetworkEnhancerConfig,
    MS1EnhancerConfig,
    SiriusEnhancerConfig,
)
from monolith.pipeline.pipeline import Pipeline
from monolith.enhancers.enhancer import Enhancer
from monolith.enhancers.taxa_enhancer import TaxaEnhancer
from monolith.enhancers.isdb_enhancer import ISDBEnhancer
from monolith.enhancers.ms1_enhancer import MS1Enhancer
from monolith.enhancers.network_enhancer import NetworkEnhancer
from monolith.enhancers.sirius_enhancer import SiriusEnhancer
from monolith.exceptions import ConfigurationError


class DefaultPipeline(Pipeline):
    """Default pipeline for ENPKG analysis."""

    enhancers: list[Type[Enhancer]]

    def __init__(
        self,
        isdb_configuration: ISDBEnhancerConfig,
        ms1_configuration: MS1EnhancerConfig,
        network_configuration: NetworkEnhancerConfig,
        sirius_configuration: SiriusEnhancerConfig,
    ):
        """Initializes the pipeline with a list of enhancers."""
        super().__init__()

        self.enhancers: list[Type[Enhancer]] = []

        # self.logger.info("Initializing taxa enhancer")
        # start = time()
        # taxa_enhancer = TaxaEnhancer()
        # self.enhancers.append(taxa_enhancer)
        # self.logger.info("%s took %.2f seconds", taxa_enhancer.name(), time() - start)

        # self.logger.info("Initializing network enhancer")
        # start = time()
        # network_enhancer = NetworkEnhancer(network_configuration)
        # self.enhancers.append(network_enhancer)
        # self.logger.info(
        #     "%s took %.2f seconds", network_enhancer.name(), time() - start
        # )

        # self.logger.info("Initializing MS1 enhancer")
        # start = time()
        # ms1_enhancer = MS1Enhancer(
        #     ms1_configuration,
        #     logger=self.logger,
        # )
        # self.enhancers.append(ms1_enhancer)
        # self.logger.info(
        #     "%s took %.2f seconds", ms1_enhancer.name(), time() - start
        # )

        # self.logger.info("Initializing ISDB enhancer")
        # start = time()
        # isdb_enhancer = ISDBEnhancer(
        #     isdb_configuration,
        #     logger=self.logger,
        # )
        # self.enhancers.append(isdb_enhancer)
        # self.logger.info(
        #     "%s took %.2f seconds", taxa_enhancer.name(), time() - start
        # )

        self.logger.info("Initializing Sirius enhancer")
        start = time()
        sirius_enhancer = SiriusEnhancer(
            sirius_configuration,
            logger=self.logger,
        )
        self.enhancers.append(sirius_enhancer)
        self.logger.info(
            "%s took %.2f seconds", sirius_enhancer.name(), time() - start
        )

    @classmethod
    def from_yaml(cls, config: str) -> "DefaultPipeline":
        """Creates a pipeline from a YAML configuration file."""

        with open(config, "r", encoding="utf-8") as file:
            global_configuration = yaml.safe_load(file)

        isdb_configuration = ISDBEnhancerConfig.from_dict(global_configuration["isdb"])
        ms1_configuration = MS1EnhancerConfig.from_dict(
            global_configuration["ms1_enhancer"]
        )
        network_configuration = NetworkEnhancerConfig.from_dict(
            global_configuration["network"]
        )
        sirius_configuration = SiriusEnhancerConfig.from_dict(
            global_configuration["sirius"]
        )

        return cls(
            isdb_configuration=isdb_configuration,
            ms1_configuration=ms1_configuration,
            network_configuration=network_configuration,
            sirius_configuration=sirius_configuration
        )

    def name(self) -> str:
        """Returns the name of the pipeline."""
        return "Default pipeline"

    def enhancers(self) -> list[Type[Enhancer]]:
        """Returns the list of enhancers."""
        return self.enhancers
