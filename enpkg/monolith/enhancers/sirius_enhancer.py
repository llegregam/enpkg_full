"""Enhancer which executes Sirius on the MGF document associated with the analysis."""

import subprocess
import os
from logging import Logger
from monolith.enhancers.enhancer import Enhancer
from monolith.data import SiriusEnhancerConfig, Analysis


class SiriusEnhancer(Enhancer):
    """Enhancer which executes Sirius on the MGF document associated with the analysis."""

    def __init__(self, config: SiriusEnhancerConfig, logger: Logger):
        """Initializes the SiriusEnhancer with the given configuration."""
        self._config = config
        self._logger = logger

        self._logger.info("Logging into Sirius.")
        check = subprocess.run(
            [
                config.path_to_sirius,
                "login",
                "--user-env",
                config.sirius_user_env,
                "--password-env",
                config.sirius_password_env,
            ],
            check=True,
        )

        if check.returncode != 0:
            raise RuntimeError("Sirius failed to login.")

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Sirius Enhancer"

    def enhance(self, analysis: Analysis) -> Analysis:
        """Enriches the given analysis with the results of the Sirius analysis."""
        result = subprocess.run(
            " ".join(
                [
                    self._config.path_to_sirius,
                    self._config.sirius_command_arg.format(
                        file=analysis.tandem_mass_spectra_for_sirius_path,
                        output_name=os.path.join(
                            self._config.output_directory, analysis.raw_hash
                        ),
                    ),
                ]
            ),
            shell=True,
            check=True,
            env={
                "SIRIUS_USER": self._config.user,
                "SIRIUS_PASSWORD": self._config.password,
            },
        )

        if result.returncode != 0:
            raise RuntimeError("Sirius failed to execute.")

        # LOAD THE SIRIUS OUTPUT INTO THE ANALYSIS

        return analysis
