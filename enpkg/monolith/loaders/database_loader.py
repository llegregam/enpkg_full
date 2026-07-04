"""
Database downloader & loader for the different enhancers.

Since the LotusStore refactor, DBLoader's sole remaining responsibilities are:
  1. Download and path-reconcile the raw artefacts the user configured.
  2. Load the spectral library (as ``list[matchms.Spectrum]``) from DuckDB.
Taxonomical compound access lives in LotusStore, and the CSV / pickle fallbacks
have been dropped in favour of the DuckDB-only path.
"""

from logging import Logger
from pathlib import Path
from time import time
from typing import NamedTuple
from urllib.parse import unquote, urlparse

from downloaders import BaseDownloader
from matchms import Spectrum

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.exceptions import DBLoaderError
from enpkg.monolith.loaders.database_manager import DatabaseManager

# Valid URL field names that can be used in redownload_if_exists
VALID_URL_FIELDS: list[str] = [
    "taxo_db_metadata",
    "spectral_db_pos",
    "spectral_db_neg",
    "taxo_db_pathways",
    "taxo_db_superclasses",
    "taxo_db_classes",
]


class DownloadInfo(NamedTuple):
    """Container for database download information."""
    field_name: str
    url: str
    local_path: str


class DBLoader:
    """
    Loader for the different databases (ISDB, Taxonomical, etc).
    """

    def __init__(self, configuration: MSEnhancerConfig, logger: Logger):

        self.configuration = configuration
        self.logger = logger
        self.downloads: list[DownloadInfo] = []
        self.spectral_db_is_loaded = False

        if self.configuration.downloader_params.urls is not None:
            self._validate_redownload_fields()
            self._collect_downloads()
            if self.downloads:
                self._download_databases()

        # Always reconcile paths (handles both fresh downloads and pre-existing files)
        self._reconcile_extracted_paths()

    def _validate_redownload_fields(self) -> None:
        """Validate that redownload_if_exists contains only valid URL field names."""

        redownload = self.configuration.downloader_params.redownload_if_exists

        # If it's a boolean, no validation needed
        if isinstance(redownload, bool):
            return

        # If it's a list, validate each field name
        if isinstance(redownload, list):
            invalid_fields = [field for field in redownload if field not in VALID_URL_FIELDS]
            if invalid_fields:
                raise DBLoaderError(
                    f"Invalid field name(s): {invalid_fields}. "
                    f"Valid fields are: {VALID_URL_FIELDS}"
                )

    def _collect_downloads(self) -> None:
        """Match URLs to their corresponding local paths.

        If download_dir is specified, paths are auto-derived from URLs.
        Otherwise, uses explicitly defined paths from configuration.
        """
        download_dir = self.configuration.downloader_params.download_dir

        for field_name, url in self.configuration.downloader_params.urls.items():
            if download_dir is not None:
                # Derive path from URL filename + download_dir
                local_path = self._derive_path_from_url(url, download_dir)
                # Update configuration paths so they're available later
                if hasattr(self.configuration.downloader_params.paths, field_name):
                    setattr(self.configuration.downloader_params.paths, field_name, local_path)
                self.downloads.append(DownloadInfo(field_name, url, local_path))
            else:
                # Use explicit path from configuration
                local_path = getattr(self.configuration.downloader_params.paths, field_name, None)
                if local_path is not None:
                    self.downloads.append(DownloadInfo(field_name, url, local_path))
                else:
                    self.logger.warning(
                        f"No local path defined for URL '{field_name}' and no download_dir set; skipping"
                    )

    def _derive_path_from_url(self, url: str, download_dir: str) -> str:
        """Extract filename from URL and combine with download directory.

        Handles query parameters (e.g., ?download=1) and URL encoding.
        Raises DBLoaderError if the URL doesn't contain a valid filename.
        """
        if not url or not url.strip():
            raise DBLoaderError("URL cannot be empty")

        parsed = urlparse(url)
        # Get the path component and extract filename
        path = unquote(parsed.path)
        filename = Path(path).name

        if not filename:
            raise DBLoaderError(
                f"Could not extract filename from URL '{url}'. "
                f"URL path component: '{parsed.path}'. "
                f"Extracted path: '{path}'. "
                f"Please ensure the URL points to a file, not a directory."
            )

        # Ensure download directory exists
        dir_path = Path(download_dir)
        dir_path.mkdir(parents=True, exist_ok=True)

        return str(dir_path / filename)

    def _download_databases(self) -> None:
        """Download databases from URLs to local paths."""

        self.logger.info(f"Downloading {len(self.downloads)} databases")
        downloader = BaseDownloader()
        for download in self.downloads:
            try:
                p = Path(download.local_path)
                redownload = self.configuration.downloader_params.redownload_if_exists
                should_redownload = (
                    redownload is True or
                    (isinstance(redownload, list) and download.field_name in redownload)
                )

                # Check if file exists (either compressed or extracted version)
                file_exists = p.is_file() or self._extracted_file_exists(p)

                if file_exists and not should_redownload:
                    self.logger.info(
                        f"Database at {download.local_path} already exists; skipping download"
                    )
                else:
                    downloader.download(download.url, download.local_path)
                    self.logger.info(f"Downloaded database from {download.url} to {download.local_path}")

            except Exception as e:
                self.logger.error(
                    f"Failed to download database from {download.url} to {download.local_path}: {str(e)}"
                )

        self.logger.info("Databases downloaded successfully")

    def _extracted_file_exists(self, file_path: Path) -> bool:
        """Check if an extracted version of the file exists.

        For compressed files like .csv.gz, checks if .csv exists.
        """
        for ext in ['.gz', '.zip', '.bz2', '.xz']:
            if str(file_path).endswith(ext):
                extracted_path = Path(str(file_path)[:-len(ext)])
                if extracted_path.is_file():
                    return True
        return False

    def _reconcile_extracted_paths(self) -> None:
        """Reconcile paths after BaseDownloader extraction.

        BaseDownloader automatically extracts compressed files:
        - Downloads `file.csv.gz` to the specified path
        - Extracts to `file.csv` (without compression extension)
        - Creates `file.csv.gz.extracted` marker file

        This method:
        1. Finds the actual extracted file (e.g., .csv.gz -> .csv)
        2. Updates configuration.paths to point to the extracted file
        3. Cleans up the '.extracted' marker files

        Works both when URLs are provided (using self.downloads) and when only
        paths are provided (checking configuration.paths directly).
        """
        # Collect all paths to reconcile: from downloads list or directly from config paths
        paths_to_reconcile: list[tuple[str, str]] = []  # (field_name, local_path)

        if self.downloads:
            paths_to_reconcile = [(d.field_name, d.local_path) for d in self.downloads]
        elif self.configuration.downloader_params.paths is not None:
            for field_name in VALID_URL_FIELDS:
                local_path = getattr(self.configuration.downloader_params.paths, field_name, None)
                if local_path is not None:
                    paths_to_reconcile.append((field_name, local_path))

        reconciled_downloads: list[DownloadInfo] = []

        for field_name, local_path in paths_to_reconcile:
            file_path = Path(local_path)
            extracted_marker = Path(f"{local_path}.extracted")
            actual_path = file_path

            # BaseDownloader extracts .gz -> removes extension
            # Check if the compressed file was extracted to a file without the extension
            if not file_path.is_file():
                for ext in ['.gz', '.zip', '.bz2', '.xz']:
                    if str(file_path).endswith(ext):
                        potential_extracted = Path(str(file_path)[:-len(ext)])
                        if potential_extracted.is_file():
                            actual_path = potential_extracted
                            self.logger.debug(
                                f"Found extracted file: {file_path} -> {actual_path}"
                            )
                            break

            # Update configuration paths to point to actual file
            if actual_path != file_path:
                if hasattr(self.configuration.downloader_params.paths, field_name):
                    setattr(self.configuration.downloader_params.paths, field_name, str(actual_path))
                    self.logger.info(
                        f"Updated path for '{field_name}': {file_path} -> {actual_path}"
                    )

            # Clean up the .extracted marker file if it exists
            if extracted_marker.is_file():
                try:
                    extracted_marker.unlink()
                    self.logger.debug(f"Removed extraction marker: {extracted_marker}")
                except OSError as e:
                    self.logger.warning(f"Could not remove marker file {extracted_marker}: {e}")

            # Rebuild downloads list entry with actual path
            if self.downloads:
                matching_download = next(
                    (d for d in self.downloads if d.field_name == field_name), None
                )
                if matching_download:
                    reconciled_downloads.append(
                        DownloadInfo(field_name, matching_download.url, str(actual_path))
                    )

        if self.downloads:
            self.downloads = reconciled_downloads

    def load_spectral_databases(self, mode: str) -> None:
        """Load the spectral library from the configured DuckDB file.

        Taxonomical compound data is not loaded here — use ``LotusStore``
        instead. The CSV / pickle fallbacks were removed: DuckDB is required.
        """
        if self.spectral_db_is_loaded:
            self.logger.info("Spectral database already loaded; skipping")
            return

        duckdb_path = self.configuration.downloader_params.duckdb_path
        if not duckdb_path:
            raise DBLoaderError(
                "A DuckDB path is required to load spectral data; CSV/pickle fallback is no longer supported."
            )
        self._load_spectral_from_duckdb(duckdb_path, mode)

        self.spectral_db_is_loaded = True

    def _load_spectral_from_duckdb(self, duckdb_path: str, mode: str) -> None:
        """Load spectral database from DuckDB."""

        self.logger.info(f"Loading {mode} spectral database from DuckDB: {duckdb_path}")
        start = time()
        with DatabaseManager(duckdb_path, read_only=True) as db:
            self.spectral_db: list[Spectrum] = db.get_spectra_by_mode(mode)
        self.logger.info(
            f"Loaded {len(self.spectral_db):,} spectra ({mode}) from DuckDB "
            f"in {time() - start:.2f}s"
        )

