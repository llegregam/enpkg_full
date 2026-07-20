"""Enhancer which executes Sirius on the MGF document associated with the analysis."""

import os
import subprocess
from datetime import datetime
from logging import Logger
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.enhancers.sirius_parser import (
    SiriusOutputParser,
    SiriusResults,
    attach_sirius_annotations,
)


class SiriusLoginInfo:
    """Class to handle the initialization of the Sirius login information."""

    def __init__(self, config: Optional[SiriusEnhancerConfig]):
            self.config = config
            self._sirius_path: Optional[str] = None
            self._user: Optional[str] = None
            self._password: Optional[str] = None
            load_dotenv()

    @property
    def sirius_path(self) -> str:
        """Returns the path to the Sirius executable."""
        if not self._sirius_path:
            self._sirius_path = os.environ.get("PATH_TO_SIRIUS")
            if self._sirius_path is None and self.config and self.config.sirius_params.path_to_sirius:
                self._sirius_path = self.config.sirius_params.path_to_sirius
            if not isinstance(self._sirius_path, str):
                raise RuntimeError(f"Path to Sirius must be of type string. Detected type: {type(self._sirius_path)}")
        return self._sirius_path

    @property
    def user(self) -> str:
        """Returns the user for the Sirius login."""
        if not self._user:
            self._user = os.environ.get("SIRIUS_USERNAME")
            if self._user is None and self.config and self.config.sirius_params.sirius_user_env:
                self._user = self.config.sirius_params.sirius_user_env
            if not isinstance(self._user, str):
                raise RuntimeError(f"Sirius user must be of type string. Detected type: {type(self._user)}")
        return self._user

    @property
    def password(self) -> str:
        """Returns the password for the Sirius login."""
        if not self._password:
            self._password = os.environ.get("SIRIUS_PASSWORD")
            if self._password is None and self.config and self.config.sirius_params.sirius_password_env:
                self._password = self.config.sirius_params.sirius_password_env
            if not isinstance(self._password, str):
                raise RuntimeError(f"Sirius password must be of type string. Detected type: {type(self._password)}")
        return self._password

    # def get_info(self):
    #     """
    #     Retrieves the Sirius login information from the environment variables.
    #     If None are defined in the environment variables, we retrieve them from the configuration file.
    #     If none are defined in the configuration file, we raise an error.
    #     """
    #     self._logger.info("Loading environment variables for Sirius login.")
    #     load_dotenv()
    #     self._sirius_path = os.environ.get("PATH_TO_SIRIUS")
    #     if self._sirius_path is None and self.config and self.config.sirius_params.path_to_sirius:
    #         self._sirius_path = self.config.sirius_params.path_to_sirius



class SiriusEnhancer(Enhancer):
    """Enhancer which executes Sirius on the MGF document associated with the analysis."""

    def __init__(self, config: SiriusEnhancerConfig, logger: Logger):
        """Initializes the SiriusEnhancer with the given configuration."""
        self.config = config
        self._logger = logger
        self._logger.debug("Path to Sirius: %s", self.config.sirius_params.path_to_sirius)

    def set_environment_variables(self, user, password):
        """Sets the environment variables for the Sirius login."""
        os.environ["SIRIUS_USER"] = user
        os.environ["SIRIUS_PASSWORD"] = password

    def _run_sirius(self, args: list[str]) -> subprocess.CompletedProcess:
        sirius_path = self.config.sirius_params.path_to_sirius
        if not sirius_path:
            raise RuntimeError(
                "Path to Sirius executable is not configured. "
                "Please set the PATH_TO_SIRIUS environment variable or configure it in the SiriusEnhancerConfig."
            )
        return subprocess.run(
            [sirius_path, *args],
            check=True,
            env=os.environ.copy(),
            shell=False,
        )

    def _get_results(self, output_path: str) -> Optional[SiriusResults]:
        """Parse the SIRIUS summary TSVs written to the summaries directory.

        Returns ``None`` when no summary files are present (e.g. SIRIUS produced
        no output). Attaching these frames onto the ``Analysis`` data model is
        the remaining half of SIRIUS ingestion (see docs/REFACTORING_PLAN.md
        F-03); for now the parsed results are returned to the caller only.
        """
        summaries_dir = Path(self.config.sirius_params.output_directory) / "summaries"
        if not summaries_dir.is_dir():
            self._logger.warning("No SIRIUS summaries directory at %s", summaries_dir)
            return None
        paths = [str(p) for p in sorted(summaries_dir.glob("*.tsv"))]
        if not paths:
            self._logger.warning("No SIRIUS summary files found in %s", summaries_dir)
            return None
        return SiriusOutputParser.digest_paths(paths).results

    def login(self) -> None:

        self._logger.info("Logging into Sirius.")
        try:
            self._run_sirius([
                "login",
                "--user-env", "SIRIUS_USERNAME",
                "--password-env", "SIRIUS_PASSWORD",
                "--show"
            ])

        except subprocess.CalledProcessError as e:
            raise RuntimeError("Error occured in subprocess while trying to log in to Sirius. Traceback: " + str(e)) from e
        except Exception as e:
            raise RuntimeError("An unexpected error occured while trying to log in to Sirius. Traceback: " + str(e)) from e
        else:
            self._logger.info("Sirius login successful.")

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Sirius Enhancer"

    def enhance(self, analysis: Analysis) -> Analysis:
        self.login()
        self._logger.info("Running Sirius enhancement.")

        # TODO: need better identifiers for the samples.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.config.general_params.polarity == "pos":
            sample_name = analysis.metadata.sample_filename_pos.split(".")[0]
        elif self.config.general_params.polarity == "neg":
            sample_name = analysis.metadata.sample_filename_neg.split(".")[0]
        else:
            raise ValueError(f"Invalid polarity: {self.config.general_params.polarity}. Must be 'pos' or 'neg'.")

        # SIRIUS 6 stores each project as a single `.sirius` file (a Nitrite database),
        # not a directory like SIRIUS 5 did. Create only the parent folder and point
        # `-o` at the file
        project_dir = Path(self.config.sirius_params.output_directory).resolve() / stamp
        project_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(project_dir / f"{sample_name}.sirius")
        db_list = (
            "public_spectra_2506,METACYC,BloodExposome,CHEBI,COCONUT,FooDB,"
            "GNPS,HMDB,HSDB,KEGG,KNAPSACK,LOTUS,LIPIDMAPS,MACONDA,MESH,MiMeDB,NORMAN,PLANTCYC,"
            "PUBCHEMANNOTATIONBIO,PUBCHEMANNOTATIONDRUG,PUBCHEMANNOTATIONFOOD,"
            "PUBCHEMANNOTATIONSAFETYANDTOXIC,SUPERNATURAL,TeroMol,YMDB"
        )
        identity_search_precursor_deviation = self.config.sirius_params.identity_search_precursor_deviation  # in ppm
        ms2_mass_deviation = self.config.sirius_params.ms2_mass_deviation  # in ppm
        n_candidates = top_k_sirius = self.config.sirius_params.top_k_sirius if hasattr(self.config.sirius_params, "top_k_sirius") and self.config.sirius_params.top_k_sirius is not None else -1


        sirius_args = [
            "--input", str(Path(self.config.sirius_params.path_to_input_spectra).resolve()),
            "-o", output_path,
            # Configuration options must be passed via the `config` subcommand;
            # they are not top-level CLI options.
            "config",
            "--AlgorithmProfile=orbitrap",
            f"--MS2MassDeviation.allowedMassDeviation={ms2_mass_deviation}ppm",
            f"--SpectralSearchDB={db_list}",
            "--AdductSettings.fallback=[[M+H]+,[M+Na]+,[M+K]+]",
            f"--NumberOfCandidates={n_candidates}",
            "--FormulaSettings.enforced=H,C,N,O,P",
            f"--IdentitySearchSettings.precursorDeviation={identity_search_precursor_deviation}ppm",
            "--FormulaSearchSettings.performBottomUpAboveMz=0",
            "--ExpansiveSearchConfidenceMode.confidenceScoreSimilarityMode=EXACT",
            "--FormulaSearchDB=",
            f"--StructureSearchDB={db_list}",
            "--SpectralSearchLog=0",
            # Tool subcommands run after `config`
            "spectra-search",
            "formulas",
            "fingerprints",
            "classes",
            "structures",
            "write-summaries",
            "--output", self.config.sirius_params.output_directory + "/summaries/",
            f"--top-k-summary={top_k_sirius}"
        ]

        if self.config.sirius_params.recompute:
            sirius_args.insert(4, "--recompute")
        # if hasattr(self.config.sirius_params, "zip_output") and self.config.sirius_params.zip_output:
        #     sirius_args.append("--zip-output")


        self._run_sirius(sirius_args)
        results = self._get_results(output_path)
        if results is None:
            self._logger.warning("No SIRIUS summaries parsed; analysis left unchanged.")
            return analysis
        # Attach the top-k structure identifications onto the spectra (joined by
        # mappingFeatureId -> feature_id) so they can be serialized into the KG.
        analysis = attach_sirius_annotations(
            analysis, results, top_k=self.config.sirius_params.top_k_sirius
        )
        n_annotated = sum(1 for spectrum in analysis.spectra if spectrum.has_sirius_annotations())
        total = sum(len(spectrum.sirius_annotations) for spectrum in analysis.spectra)
        self._logger.info(
            "Attached SIRIUS structure identifications: %d annotations across %d/%d spectra.",
            total, n_annotated, len(analysis.spectra),
        )
        return analysis
