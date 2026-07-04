"""Loader utilities for creating Analysis objects from files."""

import logging
from pathlib import Path
from typing import Optional

import polars as pl
from matchms import Spectrum
from matchms.filtering import (
    normalize_intensities,
    require_minimum_number_of_peaks,
    select_by_intensity,
    select_by_mz,
)
from matchms.importing import load_from_mgf, load_from_mzml, load_from_mzxml

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.data.sample_metadata import SampleMetadata

logger = logging.getLogger(__name__)


class AnalysisLoader:
    """Factory class for loading Analysis objects from files."""

    SUPPORTED_SPECTRA_FORMATS = {
        ".mgf": load_from_mgf,
        ".mzml": load_from_mzml,
        ".mzxml": load_from_mzxml,
    }

    RAW_EXTENSIONS = [".mzML", ".mzml", ".mzXML", ".mzxml"]

    @classmethod
    def from_files(
        cls,
        path_to_spectra: str,
        path_to_metadata: str,
        path_to_quant_table: str,
        ionization_mode: str,
        run_name: Optional[str] = None,
        *,
        clean_spectra: bool = True,
        min_peaks: int = 1,
        mz_from: float = 10.0,
        mz_to: float = 1000.0,
        min_relative_intensity: float = 0.01,
    ) -> Analysis:
        """
        Creates an Analysis object from spectra and metadata files.

        Args:
            path_to_spectra: Path to the spectra file (.mgf, .mzML, .mzXML).
            path_to_metadata: Path to the metadata file (CSV/TSV).
            path_to_quant_table: Path to the quantification table file (CSV/TSV).
            ionization_mode: Ionization mode, either 'pos' or 'neg'.
            run_name: Name of the run. If None, inferred from spectra filename.
            clean_spectra: Apply peak cleaning (normalise, m/z + relative-intensity
                windowing, minimum-peak filter) before building the Analysis.
                Ported from the legacy ``Batch.peak_processing`` path.
            min_peaks: Drop spectra left with fewer than this many peaks after
                cleaning (``require_minimum_number_of_peaks``).
            mz_from / mz_to: Keep only peaks whose m/z falls in this window.
            min_relative_intensity: After normalising to a max of 1, keep only
                peaks at or above this fraction of the base peak. 0 disables the
                intensity filter.

        Returns:
            An Analysis instance.

        Raises:
            ValueError: If file format is unsupported or run_name not found in metadata.
        """
        path_to_spectra = Path(path_to_spectra)
        path_to_metadata = Path(path_to_metadata)
        path_to_quantification_table = Path(path_to_quant_table)

        # Load spectra
        spectra = cls._load_spectra(path_to_spectra)
        if clean_spectra:
            spectra = cls._clean_spectra(
                spectra,
                mz_from=mz_from,
                mz_to=mz_to,
                min_relative_intensity=min_relative_intensity,
                min_peaks=min_peaks,
            )

        # Determine run name
        run_name = run_name or path_to_spectra.stem

        # Load and filter metadata
        metadata = cls._load_metadata(path_to_metadata, ionization_mode, run_name)

        # Load quantification table
        quant_table = cls._load_quantification_table(path_to_quantification_table)

        # Find the intensity column (contains "Peak height" or "Peak area")
        intensity_col = next(
            (col for col in quant_table.columns if "Peak height" in col or "Peak area" in col),
            None
        )
        if intensity_col is None:
            raise ValueError("No column containing 'Peak height' or 'Peak area' found in quantification table")

        # Build a single {row_id: (mz, rt, intensity)} dict so each spectrum is an
        # O(1) lookup instead of three quant_table.filter() materialisations
        # (3 × N polars frame builds was the dominant cost on large quant tables).
        # Handle null RT values (from ignore_errors=True) by replacing with NaN.
        quant_lookup: dict[int, tuple[float, float, float]] = {
            int(row[0]): (float(row[1]), float(row[2]) if row[2] is not None else float("nan"), float(row[3]))
            for row in quant_table.select(
                ["row ID", "row m/z", "row retention time", intensity_col]
            ).iter_rows()
        }

        def _build_annotated(spectrum) -> AnnotatedSpectrum:
            feature_id = int(spectrum.get("feature_id"))
            try:
                mz, rt, intensity = quant_lookup[feature_id]
            except KeyError:
                raise ValueError(
                    f"feature_id {feature_id} from spectra file not found in "
                    f"quantification table 'row ID' column"
                ) from None
            return AnnotatedSpectrum(
                spectrum=spectrum,
                mass_over_charge=mz,
                retention_time=rt,
                intensity=intensity,
            )

        return Analysis(
            run_name=run_name,
            spectra=tuple(_build_annotated(s) for s in spectra),
            metadata=metadata,
            ionization_mode=ionization_mode,
        )

    @staticmethod
    def _sniff_separator(path: Path) -> str:
        """Detect the column separator from the first non-empty line of a CSV/TSV file.

        Picks whichever of comma / semicolon / tab appears most often in the
        header row. Raises if none are present, so a malformed file fails loudly
        instead of degrading to a single-column read where every "column" name is
        the entire header glued together.
        """
        with path.open("r", encoding="utf-8") as f:
            first_line = ""
            for line in f:
                if line.strip():
                    first_line = line
                    break
        if not first_line:
            raise ValueError(f"Quantification table {path} is empty")
        counts = {sep: first_line.count(sep) for sep in (",", ";", "\t")}
        sep, count = max(counts.items(), key=lambda kv: kv[1])
        if count == 0:
            raise ValueError(
                f"Could not detect a column separator in {path}: header line "
                f"contains no commas, semicolons, or tabs."
            )
        return sep

    @classmethod
    def _load_quantification_table(cls, path: Path) -> pl.DataFrame:
        """Load quantification table from file with auto-detected separator."""
        separator = cls._sniff_separator(path)
        try:
            quant_table = pl.read_csv(path, separator=separator)
        except pl.exceptions.ComputeError as exc:
            logger.warning(
                "Quantification table parse error (likely corrupted values): %s. "
                "Re-reading with ignore_errors=True; invalid values will become null.",
                exc,
            )
            quant_table = pl.read_csv(path, separator=separator, ignore_errors=True)

        required = ("row ID", "row m/z", "row retention time")
        missing = [c for c in required if c not in quant_table.columns]
        if missing:
            raise ValueError(
                f"Quantification table {path} is missing required column(s) "
                f"{missing}. Detected separator: {separator!r}. "
                f"Columns found: {quant_table.columns}"
            )
        return quant_table

    @classmethod
    def _load_spectra(cls, path: Path) -> tuple:
        """Load spectra from file."""
        suffix = path.suffix.lower()

        loader = cls.SUPPORTED_SPECTRA_FORMATS.get(suffix)
        if loader is None:
            supported = ", ".join(cls.SUPPORTED_SPECTRA_FORMATS.keys())
            raise ValueError(
                f"Unsupported spectra format: {suffix}. Supported: {supported}"
            )

        return tuple(loader(str(path)))

    @classmethod
    def _clean_spectra(
        cls,
        spectra: tuple,
        *,
        mz_from: float,
        mz_to: float,
        min_relative_intensity: float,
        min_peaks: int,
    ) -> tuple:
        """Apply peak cleaning to each spectrum, dropping any left empty/too small.

        Ports the legacy ``Batch.peak_processing`` behaviour into the loader:
        normalise intensities, restrict to the [mz_from, mz_to] window, drop peaks
        below ``min_relative_intensity`` of the base peak, then require at least
        ``min_peaks`` peaks. Metadata (``precursor_mz``, ``feature_id``, ...) is
        untouched, so the downstream feature-id lookup and precursor check are
        unaffected. Spectra that a filter reduces to ``None`` are skipped.
        """
        cleaned = tuple(
            s
            for spectrum in spectra
            if (
                s := cls._clean_spectrum(
                    spectrum,
                    mz_from=mz_from,
                    mz_to=mz_to,
                    min_relative_intensity=min_relative_intensity,
                    min_peaks=min_peaks,
                )
            )
            is not None
        )
        dropped = len(spectra) - len(cleaned)
        if dropped:
            logger.info(
                "Peak cleaning dropped %d/%d spectra below the quality threshold",
                dropped, len(spectra),
            )
        return cleaned

    @staticmethod
    def _clean_spectrum(
        spectrum: Spectrum,
        *,
        mz_from: float,
        mz_to: float,
        min_relative_intensity: float,
        min_peaks: int,
    ) -> Optional[Spectrum]:
        """Clean one spectrum; return None if a filter removes all its peaks.

        matchms filters return ``None`` when nothing survives, so each step is
        guarded before the next is applied.
        """
        spectrum = normalize_intensities(spectrum)
        if spectrum is None:
            return None
        if min_relative_intensity > 0:
            spectrum = select_by_intensity(spectrum, intensity_from=min_relative_intensity)
            if spectrum is None:
                return None
        spectrum = select_by_mz(spectrum, mz_from=mz_from, mz_to=mz_to)
        if spectrum is None:
            return None
        return require_minimum_number_of_peaks(spectrum, n_required=min_peaks)

    @classmethod
    def _load_metadata(
        cls,
        path: Path,
        ionization_mode: str,
        run_name: str
    ) -> SampleMetadata:
        """Load and filter metadata for the given run."""
        metadata_df = pl.read_csv(path, try_parse_dates=True, separator=cls._sniff_separator(path))
        column_name = f"sample_filename_{ionization_mode}"

        for raw_ext in cls.RAW_EXTENSIONS:
            filtered = metadata_df.filter(pl.col(column_name) == (run_name + raw_ext))
            if not filtered.is_empty():
                return SampleMetadata.from_dict(filtered.to_dicts()[0])

        raise ValueError(
            f"Run name '{run_name}' not found in metadata for ionization mode '{ionization_mode}'"
        )
