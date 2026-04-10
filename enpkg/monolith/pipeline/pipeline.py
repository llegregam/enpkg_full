"""An abstract interface for pipelines to process a batch of analyses."""

from abc import ABC, abstractmethod
from typing import Type
from time import time
from logging import Logger, getLogger
from tqdm.auto import tqdm
from monolith.data.batch_class import Batch
from monolith.enhancers.enhancer import Enhancer


class Pipeline(ABC):
    """Interface for pipelines."""

    logger: Logger

    def __init__(self, enhancers: list[Enhancer]):
        """Initializes the pipeline."""
        self.logger = getLogger(self.name())
        self.logger.info("Initializing pipeline %s", self.name())
        self._enhancers = enhancers

    @abstractmethod
    def name(self) -> str:
        """Returns the name of the pipeline."""

    @abstractmethod
    def enhancers(self) -> list[Type[Enhancer]]:
        """Returns the list of enhancers."""

    def process(self, batch: Batch) -> Batch:
        """Processes the batch of analyses."""
        assert isinstance(batch, Batch)

        for enhancer in tqdm(
            self.enhancers,
            desc="Processing",
            unit="enhancer",
            leave=False,
            dynamic_ncols=True,
        ):
            start = time()
            for analysis in tqdm(
                batch.analyses,
                desc=enhancer.name(),
                unit="analysis",
                leave=False,
                dynamic_ncols=True,
            ):
                enhancer.enrich(analysis)
            total_time = time() - start
            average_time_per_analysis = total_time / len(batch.analyses)
            self.logger.info("%s took %.2f seconds", enhancer.name(), total_time)
            self.logger.info(
                "Average time per analysis: %.2f seconds", average_time_per_analysis
            )

        return batch
