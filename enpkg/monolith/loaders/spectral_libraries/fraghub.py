"""Import a FragHub CSV export into ``library_spectra``.

The import is a single ``INSERT ... SELECT ... FROM read_csv(...)``: DuckDB reads
the file and evaluates the transformations itself, so no row ever becomes a Python
object and memory stays flat regardless of file size. A 2.8 GB export with 1.45 M
spectra is read in one statement.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from enpkg.monolith.exceptions import DatabaseError
from enpkg.monolith.loaders.database_manager import DatabaseManager
from enpkg.monolith.loaders.spectral_libraries.schema import (
    COLUMN_MAP,
    KNOWN_COLUMNS,
    METADATA_COLUMNS,
    REQUIRED_COLUMNS,
    nullify,
    quote_identifier,
)
from enpkg.monolith.utils.delimiters import sniff_separator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestStats:
    """What one import did, for the CLI to report and the registry row to record."""

    n_inserted: int
    n_without_inchikey: int
    ion_mode: str
    predicted: bool
    unknown_columns: tuple[str, ...]

    @property
    def unknown_columns_text(self) -> Optional[str]:
        """``unknown_columns`` rendered for the registry, or None when there are none."""
        return ", ".join(self.unknown_columns) if self.unknown_columns else None


class FragHubCsvImporter:
    """Reads one FragHub CSV bucket into the database."""

    def __init__(
        self,
        path: Path,
        *,
        ms_level: Optional[int] = 2,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        """Prepare an import.

        Args:
            path: The FragHub CSV export.
            ms_level: Keep only spectra at this MS level; ``None`` keeps every level.
                Defaults to 2 because matching experimental MS² against library
                MS³/MS⁴ is chemically meaningless, and FragHub exports carry all of
                them in one file.
            logger_: Logger; falls back to this module's.
        """
        self.path = Path(path)
        self.ms_level = ms_level
        self.logger = logger_ or logger
        self._delimiter: Optional[str] = None
        self._columns: Optional[list[str]] = None

    # ── Validation ─────────────────────────────────────────────────────────────

    def _read_header(self, db: DatabaseManager) -> list[str]:
        """Return the export's column names, reading no data rows."""
        if self._columns is not None:
            return self._columns
        self._delimiter = sniff_separator(self.path)
        db.connection.execute(
            "SELECT * FROM read_csv(?, delim=?, header=true, all_varchar=true, "
            "sample_size=-1, ignore_errors=true) LIMIT 0",
            [str(self.path), self._delimiter],
        )
        self._columns = [d[0] for d in db.connection.description]
        return self._columns

    def validate(self, db: DatabaseManager) -> tuple[str, bool, tuple[str, ...]]:
        """Check the column contract and the bucket's homogeneity.

        A FragHub bucket is segregated by ion mode and by predicted/experimental
        status, so both are properties of the library rather than of each spectrum.
        That is verified here rather than trusted: the export's filenames are
        inconsistently cased and cannot be relied on, and a mixed bucket would
        silently give every spectrum the wrong mode or provenance.

        Returns:
            ``(ion_mode, predicted, unknown_columns)``.

        Raises:
            DatabaseError: If a required column is absent, if the file is empty, or
                if the bucket mixes ion modes or predicted/experimental spectra.
        """
        columns = self._read_header(db)
        missing = sorted(REQUIRED_COLUMNS - set(columns))
        if missing:
            raise DatabaseError(
                f"{self.path} is missing required column(s) {', '.join(missing)}. "
                f"Columns found: {', '.join(columns)}."
            )

        unknown = tuple(sorted(set(columns) - KNOWN_COLUMNS))
        if unknown:
            self.logger.info(
                "%s carries %d column(s) not in the known FragHub schema; "
                "preserving them in metadata_json: %s",
                self.path.name, len(unknown), ", ".join(unknown),
            )

        has_predicted = "PREDICTED" in columns
        predicted_expr = "count(DISTINCT lower(trim(PREDICTED)))" if has_predicted else "0"
        row = db.connection.execute(
            f"SELECT count(*), count(DISTINCT lower(trim(IONMODE))), {predicted_expr}, "
            "min(lower(trim(IONMODE))), "
            f"{'min(lower(trim(PREDICTED)))' if has_predicted else 'NULL'} "
            "FROM read_csv(?, delim=?, header=true, all_varchar=true, "
            "sample_size=-1, ignore_errors=true)",
            [str(self.path), self._delimiter],
        ).fetchone()
        n_rows, n_modes, n_predicted, ion_mode_raw, predicted_raw = row

        if not n_rows:
            raise DatabaseError(f"{self.path} contains no rows.")
        if n_modes != 1:
            raise DatabaseError(
                f"{self.path} mixes {n_modes} ionization modes. A FragHub bucket is "
                "segregated by mode; import each bucket separately."
            )
        if has_predicted and n_predicted > 1:
            raise DatabaseError(
                f"{self.path} mixes predicted and experimental spectra "
                f"({n_predicted} distinct PREDICTED values). A FragHub bucket is "
                "segregated by provenance; import each bucket separately."
            )

        ion_mode = "pos" if str(ion_mode_raw).startswith("pos") else "neg"
        predicted = str(predicted_raw).strip().lower() in ("true", "1", "yes")
        self.logger.info(
            "%s: %d rows, ion_mode=%s, predicted=%s", self.path.name,
            n_rows, ion_mode, predicted,
        )
        return ion_mode, predicted, unknown

    # ── SQL construction ───────────────────────────────────────────────────────

    def _select_sql(self, columns: list[str], start_id: int, library_id: int) -> str:
        """Build the SELECT that turns FragHub rows into ``library_spectra`` rows."""
        present = set(columns)

        def value(source: str) -> str:
            """Normalised source column, or NULL when the export lacks it."""
            return nullify(source) if source in present else "NULL"

        peaks = quote_identifier("PEAKS_LIST")
        # Peaks arrive as "mz intensity;mz intensity;..." in one cell. str_split on
        # ';' gives the pairs and split_part on ' ' the two values, so both FLOAT[]
        # columns are built without leaving SQL.
        mzs = (
            f"list_transform(str_split({peaks}, ';'), "
            "x -> TRY_CAST(split_part(x, ' ', 1) AS FLOAT))"
        )
        intensities = (
            f"list_transform(str_split({peaks}, ';'), "
            "x -> TRY_CAST(split_part(x, ' ', 2) AS FLOAT))"
        )
        inchikey = value("INCHIKEY")

        projections = [
            f"{start_id} + row_number() OVER () - 1 AS id",
            f"{library_id} AS library_id",
            "CASE WHEN lower(trim(IONMODE)) LIKE 'pos%' THEN 'pos' ELSE 'neg' END AS mode",
            "TRY_CAST(TRY_CAST(MSLEVEL AS DOUBLE) AS UTINYINT) AS ms_level",
            "TRY_CAST(PRECURSORMZ AS DOUBLE) AS precursor_mz",
            f"{mzs} AS mzs",
            f"{intensities} AS intensities",
            # short_inchikey must equal Python's inchikey[:14] (Lotus.short_inchikey
            # derives the LOTUS join key that way). DuckDB slicing is 1-based and
            # inclusive, so [1:14] is the same 14 characters.
            f"upper({inchikey})[1:14] AS short_inchikey",
            f"TRY_CAST({value('EXACTMASS')} AS DOUBLE) AS exact_mass",
        ]
        projections.extend(
            f"{value(source)} AS {destination}"
            for source, destination in COLUMN_MAP.items()
        )

        # Known provenance columns, plus anything the export carries that this
        # schema does not recognise — that is what "extras allowed" means in
        # practice: an unrecognised column is stored rather than dropped, so a
        # FragHub release that adds a field loses nothing.
        metadata_fields = [c for c in METADATA_COLUMNS if c in present]
        metadata_fields.extend(sorted(present - KNOWN_COLUMNS))
        if metadata_fields:
            packed = ", ".join(
                f"{quote_identifier(c)} := {value(c)}" for c in metadata_fields
            )
            projections.append(f"to_json(struct_pack({packed})) AS metadata_json")
        else:
            projections.append("NULL AS metadata_json")

        # TRY_CAST rather than a bare cast so one malformed value nulls a single
        # field instead of aborting an hour-long import.
        where = [
            "TRY_CAST(PRECURSORMZ AS DOUBLE) IS NOT NULL",
            f"{peaks} IS NOT NULL",
        ]
        if self.ms_level is not None:
            where.append(f"TRY_CAST(MSLEVEL AS DOUBLE) = {int(self.ms_level)}")

        return (
            "SELECT " + ", ".join(projections)
            + " FROM read_csv(?, delim=?, header=true, all_varchar=true, "
              "sample_size=-1, ignore_errors=true)"
            + " WHERE " + " AND ".join(where)
        )

    # ── Import ─────────────────────────────────────────────────────────────────

    def ingest(self, db: DatabaseManager, library_id: int) -> IngestStats:
        """Insert the export's spectra, returning what was written.

        Assumes :meth:`validate` has already run against the same ``db``.
        """
        columns = self._read_header(db)
        ion_mode, predicted, unknown = self.validate(db)
        start_id = db.next_spectrum_id()

        destinations = [
            "id", "library_id", "mode", "ms_level", "precursor_mz", "mzs",
            "intensities", "short_inchikey", "exact_mass",
            *COLUMN_MAP.values(),
            "metadata_json",
        ]
        sql = (
            f"INSERT INTO library_spectra ({', '.join(destinations)}) "
            + self._select_sql(columns, start_id, library_id)
        )

        db.drop_library_spectra_indexes()
        try:
            db.connection.execute(sql, [str(self.path), self._delimiter])
            n_inserted = db.connection.execute(
                "SELECT count(*) FROM library_spectra WHERE library_id = ?",
                [library_id],
            ).fetchone()[0]
            n_without_inchikey = db.connection.execute(
                "SELECT count(*) FROM library_spectra "
                "WHERE library_id = ? AND short_inchikey IS NULL",
                [library_id],
            ).fetchone()[0]
        finally:
            # Recreate the indexes even on failure: leaving the table unindexed
            # would silently make every later query a full scan.
            db.create_library_spectra_indexes()

        if n_without_inchikey:
            self.logger.warning(
                "%d of %d spectra have no InChIKey and cannot be linked to LOTUS. "
                "An InChIKey cannot be recomputed here (no InChI toolkit is "
                "installed); re-export from FragHub if this is unexpected.",
                n_without_inchikey, n_inserted,
            )

        self.logger.info("Inserted %d spectra for library_id=%d", n_inserted, library_id)
        return IngestStats(
            n_inserted=n_inserted,
            n_without_inchikey=n_without_inchikey,
            ion_mode=ion_mode,
            predicted=predicted,
            unknown_columns=unknown,
        )

    def preview(self, db: DatabaseManager, limit: int = 20) -> list[dict]:
        """Resolve the first ``limit`` rows without inserting anything.

        Validates the header and the bucket first, so a malformed export is
        diagnosed in seconds rather than after an hour-long import.
        """
        columns = self._read_header(db)
        self.validate(db)
        sql = self._select_sql(columns, start_id=0, library_id=0) + f" LIMIT {int(limit)}"
        rows = db.connection.execute(sql, [str(self.path), self._delimiter]).fetchall()
        names = [d[0] for d in db.connection.description]
        return [dict(zip(names, row, strict=True)) for row in rows]
