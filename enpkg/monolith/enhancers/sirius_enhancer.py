"""Enhancer which executes Sirius on the MGF document associated with the analysis."""

import subprocess
import os
from logging import Logger
from tabnanny import check
from typing import Optional

# from PySirius import AccountCredentials
from dotenv import load_dotenv

from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.configuration.sirius_enhancer_config import SiriusEnhancerConfig, SiriusParams
from enpkg.monolith.data.analysis import Analysis


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
        print(f"path to Sirius: {self.config.sirius_params.path_to_sirius}")
    
    def set_environment_variables(self, user, password):
        """Sets the environment variables for the Sirius login."""
        os.environ["SIRIUS_USER"] = user
        os.environ["SIRIUS_PASSWORD"] = password
        
    def _run_sirius(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.config.sirius_params.path_to_sirius, *args],
            check=True,
            env=os.environ.copy(),
            shell=False,
        )

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
            raise RuntimeError("Error occured in subprocess while trying to log in to Sirius. Traceback: " + str(e))
        except Exception as e:
            raise RuntimeError("An unexpected error occured while trying to log in to Sirius. Traceback: " + str(e))
        else:
            self._logger.info("Sirius login successful.")

    # def login(self):
    #     """
    #      Logs into Sirius using the provided login information.
    #     """
    #     self._logger.info("Logging into Sirius.")
    #     self._logger.info(f"Using Sirius password: {'*' * len(self.config.sirius_params.sirius_password_env) if self.config.sirius_params.sirius_password_env else None}")
    #     check = self._run_sirius(
    #         [
    #             self.config.sirius_params.path_to_sirius,
    #             "login",
    #             "--user", os.environ.get("SIRIUS_USER", self.config.sirius_params.sirius_user_env),
    #             "--password-env",
    #             "SIRIUS_PASSWORD",
    #         ]
    #     )

    #     if check.returncode != 0:
    #         raise RuntimeError("Sirius failed to login.")
    #     self._logger.info("Sirius login successful.")
        
    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Sirius Enhancer"
    
    def enhance(self, analysis: Analysis) -> Analysis:

        
        self.login()
        self._logger.info("Running Sirius enhancement.")
        # TODO: need better identifiers for the samples. 
        if self.config.general_params.polarity == "pos":
            output_path = os.path.join(self.config.sirius_params.output_directory, analysis.metadata.sample_filename_pos.split(".")[0])
        elif self.config.general_params.polarity == "neg":
            output_path = os.path.join(self.config.sirius_params.output_directory, analysis.metadata.sample_filename_neg.split(".")[0])
        else:
            raise ValueError(f"Invalid polarity: {self.config.general_params.polarity}. Must be 'pos' or 'neg'.")
        
        sirius_args = [
            "--input", self.config.sirius_params.path_to_input_spectra,
            "-o", output_path,
            "formula",
            "-p",
            "orbitrap",
            "fingerprint",
            "canopus",
            "structure",
            "--database",
            "pubchem",
            "write-summaries",
            "--output",
            self.config.sirius_params.output_directory + "/summaries/"
        ]

        if self.config.sirius_params.recompute:
            sirius_args.append("--recompute")
        if self.config.sirius_params.zip_output:
            sirius_args.append("--zip-output")
        
            
        self._run_sirius(sirius_args)
        return analysis
    
if __name__ == "__main__":

    import logging
    from enpkg.monolith.loaders.analysis_loader import AnalysisLoader

    logger = logging.getLogger("SiriusEnhancer")
    logging.basicConfig(level=logging.DEBUG)
    analysis = AnalysisLoader.from_files(
        path_to_spectra="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/test-data/actea_EtOAc-1_pos.mgf",
        path_to_metadata="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/test-data/qualome_metadata.txt",
        path_to_quant_table="/home/llegregam/git_projects/enpkg_full/enpkg/monolith/test-data/actea_EtOAc-1_pos_quant.csv",
        ionization_mode="pos"
    )
    logger.info(f"Analysis metadata: {analysis.metadata}")
    enhancer = SiriusEnhancer(
        logger=logger,
        config=SiriusEnhancerConfig(
            sirius_params=SiriusParams(
                path_to_sirius=os.environ.get("PATH_TO_SIRIUS", None),
                path_to_input_spectra="/home/llegregam/git_projects/enpkg_full/enpkg/tests/test-data/enpkg_toy_dataset/msdata/processed/VGF138_A01_pos_sirius.mgf",
                output_directory="/home/llegregam/git_projects/enpkg_full/enpkg/tests/sirius_out"
            )
        )
    )
    analysis = enhancer.enhance(analysis)

    # from PySirius import SiriusSDK
    # sdk = SiriusSDK()
    # # SIRIUS must be in the path or the SIRIUS_EXE environment variable must be specified.
    # # Is automatically configured when installing via conda or windows ms installer
    # api = sdk.attach_or_start_sirius()

    # if api.actuator().health().get('status') != "UP":
    #     print("There seems to be a problem reaching the REST service!")
    #     exit(1)
    # else:
    #     print("Sirius REST service is up and running!")
    #     accept_terms = True  # ensure you accept the terms
    #     account_credentials = AccountCredentials(username=os.environ.get("SIRIUS_USERNAME"), password=os.environ.get("SIRIUS_PASSWORD"))
    #     print(f"Using Sirius username: {account_credentials.username}")
    #     print(f"Using Sirius password: {account_credentials.password}")
    #     api.account().login(accept_terms, account_credentials)