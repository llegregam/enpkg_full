"""
Database downloader & loader for the different enhancers.
"""

from typing import NamedTuple
from pathlib import Path
from urllib.parse import urlparse, unquote
import pickle
from logging import Logger
from time import time

import numpy as np
import polars as pl
from matchms import Spectrum
from downloaders import BaseDownloader

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
        self.taxo_is_loaded = False
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
        """
        parsed = urlparse(url)
        # Get the path component and extract filename
        path = unquote(parsed.path)
        filename = Path(path).name
        
        if not filename:
            raise DBLoaderError(f"Could not extract filename from URL: {url}")
        
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

    def load_taxonomical_databases(self) -> None:
        """Load taxonomical databases into memory.

        Uses DuckDB if ``duckdb_path`` is set in configuration and duckdb is
        installed; otherwise falls back to reading the original CSV files.
        The public attributes (``lotus_metadata``, ``lotus_metadata_pathways``,
        ``lotus_metadata_superclasses``, ``lotus_metadata_classes``) are always
        populated with the same types regardless of which path is taken.
        """
        self.logger.info("Loading databases into memory")
        if self.taxo_is_loaded:
            self.logger.info("Taxonomical databases already loaded; skipping")
            return

        duckdb_path = self.configuration.downloader_params.duckdb_path
        if duckdb_path:
            self._load_taxo_from_duckdb(duckdb_path)
        else:
            self._load_taxo_from_csv()

        self.taxo_is_loaded = True

    def _load_taxo_from_duckdb(self, duckdb_path: str) -> None:
        """Load taxonomical data from a pre-built DuckDB file."""

        self.logger.info(f"Loading taxonomical databases from DuckDB: {duckdb_path}")
        start = time()

        with DatabaseManager(duckdb_path, read_only=True) as db:
            t0 = time()
            if not db.is_populated():
                self.logger.warning(
                    "DuckDB file exists but compounds table is empty; falling back to CSV"
                )
                self._load_taxo_from_csv()
                return
            self.logger.info(f"  is_populated() check: {time() - t0:.2f}s")

            t0 = time()
            full_df: pl.DataFrame = db.get_all_compounds()
            self.logger.info(f"  get_all_compounds(): {time() - t0:.2f}s ({len(full_df):,} rows)")

            t0 = time()
            pathway_cols: list[str] = db.get_pathway_column_names()
            superclasses_cols: list[str] = db.get_superclass_column_names()
            classes_cols: list[str] = db.get_class_column_names()
            self.logger.info(f"  get column names: {time() - t0:.2f}s")

        self.logger.info(f"Loaded taxonomical databases in: {time() - start:.2f}s")

        # Split back into the four DataFrames that the rest of the pipeline expects.
        # The compound columns are everything except the three LIST columns appended by the JOIN.
        t0 = time()
        # TODO: Think about generalizing these colomn headers
        list_cols = {"pathways", "superclasses", "classes"}
        compound_col_names = [c for c in full_df.columns if c not in list_cols]
        self.lotus_metadata = full_df.select(compound_col_names)
        self.logger.info(f"  column split + select: {time() - t0:.2f}s")

        # Reconstruct the classification DataFrames in the original format:
        # first column = structure_smiles, remaining columns = one float per class.
        # We explode the LIST columns back into individual float columns.

        def _expand_list_col(col_name: str, col_labels: list[str]) -> pl.DataFrame:
            """Turn a FLOAT[] column into a DataFrame with one column per label."""
            return full_df.select(
                [pl.col("structure_smiles")] +
                [pl.col(col_name).list.get(i, null_on_oob=True).alias(label)
                 for i, label in enumerate(col_labels)]
            )

        t0 = time()
        self.lotus_metadata_pathways = _expand_list_col("pathways",    pathway_cols)
        self.logger.info(f"  expand pathways ({len(pathway_cols)} cols): {time() - t0:.2f}s")

        t0 = time()
        self.lotus_metadata_superclasses = _expand_list_col("superclasses", superclasses_cols)
        self.logger.info(f"  expand superclasses ({len(superclasses_cols)} cols): {time() - t0:.2f}s")

        t0 = time()
        self.lotus_metadata_classes     = _expand_list_col("classes",     classes_cols)
        self.logger.info(f"  expand classes ({len(classes_cols)} cols): {time() - t0:.2f}s")

        self._number_of_pathways    = len(pathway_cols)
        self._pathways              = pathway_cols
        self._number_of_superclasses = len(superclasses_cols)
        self._superclasses          = superclasses_cols
        self._number_of_classes     = len(classes_cols)
        self._classes               = classes_cols

        self.logger.debug(f"Taxonomical columns: {self.lotus_metadata.columns}")
        self.logger.info(
            "Loaded %d Taxonomical Database metadata entries from DuckDB (total: %.2fs)",
            len(self.lotus_metadata),
            time() - start,
        )

    def _load_taxo_from_csv(self) -> None:
        """Load taxonomical data from the original CSV files (fallback if DuckDB is not populated or available)."""
        start = time()
        # infer_schema_length=10000 ensures stable typing for Polars.
        self.lotus_metadata: pl.DataFrame = pl.read_csv(
            self.configuration.downloader_params.paths.taxo_db_metadata,
            infer_schema_length=10000,
            null_values=["", "NA", "NaN"]
        )
        self.logger.info(f"Loaded Taxonomical Database metadata in {time() - start:.2f} seconds")
        self.logger.debug(f"Loaded Taxonomical Database metadata with columns: {self.lotus_metadata.columns}")

        start = time()
        self.lotus_metadata_pathways: pl.DataFrame = pl.read_csv(
            self.configuration.downloader_params.paths.taxo_db_pathways
        )
        self.logger.info(f"Loaded Taxonomical Database pathways in {time() - start:.2f} seconds")
        self._number_of_pathways = self.lotus_metadata_pathways.shape[1] - 1
        self._pathways = self.lotus_metadata_pathways.columns[1:]

        start = time()
        self.lotus_metadata_superclasses: pl.DataFrame = pl.read_csv(
            self.configuration.downloader_params.paths.taxo_db_superclasses
        )
        self.logger.info(f"Loaded Taxonomical Database superclasses in {time() - start:.2f} seconds")
        self._number_of_superclasses = self.lotus_metadata_superclasses.shape[1] - 1
        self._superclasses = self.lotus_metadata_superclasses.columns[1:]

        start = time()
        self.lotus_metadata_classes: pl.DataFrame = pl.read_csv(
            self.configuration.downloader_params.paths.taxo_db_classes
        )
        self.logger.info(f"Loaded Taxonomical Database classes in {time() - start:.2f} seconds")
        self._number_of_classes = self.lotus_metadata_classes.shape[1] - 1
        self._classes = self.lotus_metadata_classes.columns[1:]

        self.logger.info(
            "Loaded %d Taxonomical Database metadata entries",
            len(self.lotus_metadata),
        )

    def load_spectral_databases(self, mode: str) -> None:
        """Load spectral databases into memory.

        Uses DuckDB if ``duckdb_path`` is configured and duckdb is installed;
        otherwise falls back to loading the pickle file.
        """
        if self.spectral_db_is_loaded:
            self.logger.info("Spectral database already loaded; skipping")
            return

        duckdb_path = self.configuration.downloader_params.duckdb_path
        if duckdb_path:
            self._load_spectral_from_duckdb(duckdb_path, mode)
        else:
            self._load_spectral_from_pkl(mode)

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

    def _load_spectral_from_pkl(self, mode: str) -> None:
        """Load spectral database from a pickle file (original behaviour)."""
        start = time()
        if mode == "pos":
            with open(self.configuration.downloader_params.paths.spectral_db_pos, "rb") as f:
                self.spectral_db: list[Spectrum] = pickle.load(f)
        elif mode == "neg":
            with open(self.configuration.downloader_params.paths.spectral_db_neg, "rb") as f:
                self.spectral_db: list[Spectrum] = pickle.load(f)
        else:
            raise ValueError(f"Invalid mode '{mode}' for loading spectral database")
        self.logger.debug(f"Loaded {mode} mode spectral database in {time() - start:.2f} seconds")
