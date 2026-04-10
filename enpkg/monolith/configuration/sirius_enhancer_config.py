"""Configuration for Sirius."""

from pydantic import BaseModel, Field

from enpkg.monolith.configuration.config import EnhancerConfig

class SiriusParams(BaseModel):
    """Parameters for the Sirius Enhancer."""

    path_to_sirius: str = Field(
        default=None,
        description="Path to the Sirius executable or JAR file."
    )
    sirius_command_arg: str = Field(
        default="",
        description="Additional command-line arguments to pass to Sirius."
    )
    path_to_input_spectra: str = Field(
        description="Path to the input spectra file."
    )
    recompute: bool = Field(
        default=False,
        description="Whether to recompute results even if they already exist."
    )
    output_directory: str = Field(
        default="sirius_output",
        description="Directory where Sirius outputs will be stored."
    )
    zip_output: bool = Field(
        default=True,
        description="Whether to zip the output directory after processing."
    )
    sirius_user_env: str = Field(
        default="SIRIUS_USER",
        description="Environment variable name for the Sirius API user. If passed in, passed in value will have priority."
    )
    sirius_password_env: str = Field(
        default="SIRIUS_PASSWORD",
        description="Environment variable name for the Sirius API password. If passed in, passed in value will have priority."
    )


class SiriusEnhancerConfig(EnhancerConfig):
    """Dataclass for the Sirius Enhancer configuration."""

    sirius_params: SiriusParams = Field(
        default_factory=SiriusParams,
        description="Parameters for the Sirius enhancement."
    )

    # @classmethod
    # def from_dict(cls, config: dict) -> "SiriusEnhancerConfig":
    #     """Create a SiriusEnhancerConfig from a dictionary."""
    #     return cls(
    #         path_to_sirius=config["paths"]["path_to_sirius"],
    #         output_directory=config["paths"]["output_directory"],
    #         sirius_command_arg=config["options"]["sirius_command_arg"],
    #         recompute=config["options"]["recompute"],
    #         zip_output=config["options"]["zip_output"],
    #         sirius_user_env=config["options"]["sirius_user_env"],
    #         sirius_password_env=config["options"]["sirius_password_env"],
    #     )

    # @computed_field
    # @property
    # def user(self) -> str:
    #     """Returns the user for the Sirius API."""
    #     if self.sirius_params.sirius_user_env is None:
    #         load_dotenv()
    #         user: Optional[str] = os.environ.get("SIRIUS_USERNAME")
    #     else:
    #         user : str = self.sirius_params.sirius_user_env
    #     if not isinstance(user, str) :
    #         raise RuntimeError(f"User must be of type string. Detected type: {type(user)}")
    #     return user

    # @computed_field
    # @property
    # def password(self) -> str:
    #     """Returns the password for the Sirius API."""
    #     if self.sirius_params.sirius_password_env is None:
    #         load_dotenv()
    #         password: Optional[str] = os.environ.get("SIRIUS_PASSWORD")
    #     else:
    #         password : str = self.sirius_params.sirius_password_env
    #     if not isinstance(password, str) :
    #         raise RuntimeError(f"Password must be of type string. Detected type: {type(password)}")
    #     return password
