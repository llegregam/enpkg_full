"""
Persistent DuckDB wrapper for LOTUS compound metadata and the spectral libraries.

This module provides a DatabaseManager class that:
  - Owns a file-backed DuckDB connection
  - Creates the schema. Four tables: ``compounds`` and ``npc_classifications``
    hold LOTUS; ``spectral_library_registry`` holds one row per registered
    spectral library, and ``library_spectra`` holds that library's spectra.
  - Exposes query methods returning Polars DataFrames or column-keyed row dicts

LotusStore consumes the compound queries; SpectralLibraryStore consumes the
spectral ones. Neither table is written from the pipeline: both are populated
ahead of time by the ``import_lotus`` and ``import_spectral_library`` scripts.
"""

import logging
from time import time
from typing import Optional, Sequence

import duckdb
import numpy as np
import polars as pl
from matchms import Spectrum

logger = logging.getLogger(__name__)


# Each statement is quoted separately rather than being split out of one string on
# ';'. A text split has no model of SQL, so a semicolon inside a string literal, a
# CHECK expression or a comment would divide a statement into two invalid fragments
# and report the failure against the fragment rather than against the statement as
# written.
_DDL_STATEMENTS: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS compounds (
    structure_smiles                        TEXT PRIMARY KEY,
    structure_inchikey                      TEXT,
    short_inchikey                          TEXT NOT NULL,
    structure_inchi                         TEXT,
    structure_molecular_formula             TEXT,
    structure_exact_mass                    DOUBLE,
    structure_xlogp                         DOUBLE,
    "structure_smiles_2D"                   TEXT,
    structure_cid                           TEXT,
    "structure_nameIupac"                   TEXT,
    "structure_nameTraditional"             TEXT,
    structure_stereocenters_total           INTEGER,
    structure_stereocenters_unspecified     INTEGER,
    structure_taxonomy_classyfire_chemontid TEXT,
    structure_taxonomy_classyfire_01kingdom TEXT,
    structure_taxonomy_classyfire_02superclass TEXT,
    structure_taxonomy_classyfire_03class   TEXT,
    structure_taxonomy_classyfire_04directparent TEXT,
    structure_wikidata                      TEXT,
    organism_wikidata                       TEXT,
    organism_name                           TEXT,
    organism_taxonomy_gbifid                TEXT,
    organism_taxonomy_ncbiid                TEXT,
    organism_taxonomy_ottid                 TEXT,
    organism_taxonomy_01domain              TEXT,
    organism_taxonomy_02kingdom             TEXT,
    organism_taxonomy_03phylum              TEXT,
    organism_taxonomy_04class               TEXT,
    organism_taxonomy_05order               TEXT,
    organism_taxonomy_06family              TEXT,
    organism_taxonomy_07tribe               TEXT,
    organism_taxonomy_08genus               TEXT,
    organism_taxonomy_09species             TEXT,
    organism_taxonomy_10varietas            TEXT,
    reference_wikidata                      TEXT,
    reference_doi                           TEXT,
    manual_validation                       BOOLEAN
)
""",
    """
CREATE INDEX IF NOT EXISTS idx_compounds_formula
    ON compounds (structure_molecular_formula)
""",
    """
CREATE INDEX IF NOT EXISTS idx_compounds_short_inchikey
    ON compounds (short_inchikey)
""",
    """
CREATE INDEX IF NOT EXISTS idx_compounds_exact_mass
    ON compounds (structure_exact_mass)
""",
    """
CREATE TABLE IF NOT EXISTS npc_classifications (
    structure_smiles TEXT PRIMARY KEY
        REFERENCES compounds (structure_smiles),
    pathways     FLOAT[],
    superclasses FLOAT[],
    classes      FLOAT[]
)
""",
    # One row per registered spectral library. Every library is a FragHub export,
    # and a FragHub bucket is homogeneous in ion mode and predicted/experimental
    # status, so both are recorded once here rather than per spectrum. The import
    # verifies that homogeneity rather than trusting the bucket's name.
    """
CREATE TABLE IF NOT EXISTS spectral_library_registry (
    library_id         SMALLINT PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    version            TEXT NOT NULL,
    ion_mode           TEXT NOT NULL CHECK (ion_mode IN ('pos', 'neg')),
    predicted          BOOLEAN NOT NULL,
    separation         TEXT,
    fraghub_version    TEXT,
    source_path        TEXT,
    ms_level_filter    TEXT,
    n_spectra          INTEGER,
    n_without_inchikey INTEGER,
    unknown_columns    TEXT,
    imported_at        TIMESTAMP DEFAULT current_timestamp
)
""",
    # A column here iff it is filtered on, joined on, or read to build an
    # MS2ChemicalAnnotation; everything else FragHub carries stays in
    # metadata_json. short_inchikey is nullable and derived from inchikey.
    """
CREATE TABLE IF NOT EXISTS library_spectra (
    id                    BIGINT PRIMARY KEY,
    library_id            SMALLINT NOT NULL REFERENCES spectral_library_registry (library_id),
    mode                  TEXT NOT NULL CHECK (mode IN ('pos', 'neg')),
    ms_level              UTINYINT,
    precursor_mz          DOUBLE NOT NULL,
    mzs                   FLOAT[] NOT NULL,
    intensities           FLOAT[] NOT NULL,
    inchikey              TEXT,
    short_inchikey        TEXT,
    smiles                TEXT,
    molecular_formula     TEXT,
    compound_name         TEXT,
    exact_mass            DOUBLE,
    adduct                TEXT,
    splash                TEXT,
    npc_pathway           TEXT,
    npc_superclass        TEXT,
    npc_class             TEXT,
    classyfire_superclass TEXT,
    classyfire_class      TEXT,
    classyfire_subclass   TEXT,
    metadata_json         JSON
)
""",
    """
CREATE INDEX IF NOT EXISTS idx_library_spectra_precursor_mz
    ON library_spectra (precursor_mz, mode)
""",
    """
CREATE INDEX IF NOT EXISTS idx_library_spectra_short_inchikey
    ON library_spectra (short_inchikey)
""",
    """
CREATE INDEX IF NOT EXISTS idx_library_spectra_library_id
    ON library_spectra (library_id)
""",
)

# Indexes on library_spectra, dropped before a bulk import and recreated after:
# every insert would otherwise have to update them as well.
_LIBRARY_SPECTRA_INDEXES: tuple[str, ...] = (
    "idx_library_spectra_precursor_mz",
    "idx_library_spectra_short_inchikey",
    "idx_library_spectra_library_id",
)

# Columns in the compounds table that map 1-to-1 to the original CSV columns.
# Order matters: LotusStore builds its column-to-index map from this list when
# constructing Lotus objects via Lotus.from_row(columns, ...).
_COMPOUND_COLUMNS = [
    "structure_wikidata",
    "structure_inchikey",
    "structure_inchi",
    "structure_smiles",
    "structure_molecular_formula",
    "structure_exact_mass",
    "structure_xlogp",
    "structure_smiles_2D",
    "structure_cid",
    "structure_nameIupac",
    "structure_nameTraditional",
    "structure_stereocenters_total",
    "structure_stereocenters_unspecified",
    "structure_taxonomy_classyfire_chemontid",
    "structure_taxonomy_classyfire_01kingdom",
    "structure_taxonomy_classyfire_02superclass",
    "structure_taxonomy_classyfire_03class",
    "structure_taxonomy_classyfire_04directparent",
    "organism_wikidata",
    "organism_name",
    "organism_taxonomy_gbifid",
    "organism_taxonomy_ncbiid",
    "organism_taxonomy_ottid",
    "organism_taxonomy_01domain",
    "organism_taxonomy_02kingdom",
    "organism_taxonomy_03phylum",
    "organism_taxonomy_04class",
    "organism_taxonomy_05order",
    "organism_taxonomy_06family",
    "organism_taxonomy_07tribe",
    "organism_taxonomy_08genus",
    "organism_taxonomy_09species",
    "organism_taxonomy_10varietas",
    "reference_wikidata",
    "reference_doi",
    "manual_validation",
]

# Columns whose mixed-case names must be double-quoted in SQL to preserve case.
_QUOTED_COMPOUND_COLUMNS = frozenset(
    {"structure_smiles_2D", "structure_nameIupac", "structure_nameTraditional"}
)

# Shared compound SELECT, derived from _COMPOUND_COLUMNS so the projected order
# can never drift from the column-index contract LotusStore relies on: compound
# columns first (in _COMPOUND_COLUMNS order), then pathways/superclasses/classes.
# Callers append their own ORDER BY / WHERE clause.
_COMPOUND_SELECT = (
    "SELECT "
    + ", ".join(
        f'c."{col}"' if col in _QUOTED_COMPOUND_COLUMNS else f"c.{col}"
        for col in _COMPOUND_COLUMNS
    )
    + ", n.pathways, n.superclasses, n.classes "
    + "FROM compounds c LEFT JOIN npc_classifications n USING (structure_smiles)"
)


class DatabaseManager:
    """
    Wraps a persistent DuckDB connection for LOTUS compound and spectral data.

    Usage as a context manager (preferred because it ensures proper cleanup):
        with DatabaseManager("/path/to/enpkg.duckdb") as db:
            df = db.get_all_compounds()

    Usage without context manager:
        db = DatabaseManager("/path/to/enpkg.duckdb")
        ...
        db.close()
    """

    def __init__(self, duckdb_path: str, read_only: bool = False) -> None:
        logger.debug("Connecting to DuckDB: %s (read_only=%s)", duckdb_path, read_only)
        self._path = duckdb_path
        self._conn = duckdb.connect(duckdb_path, read_only=read_only)

    def __enter__(self) -> "DatabaseManager":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The underlying DuckDB connection.

        An escape hatch for maintenance tasks that need statements this class
        does not wrap — ``ATTACH`` and cross-database copies, for instance.
        Pipeline code should use the query methods below instead, so the SQL
        stays in one place.
        """
        return self._conn

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("DuckDB connection closed: %s", self._path)

    # ── Schema ────────────────────────────────────────────────────────────────

    def create_schema(self) -> None:
        """Create all tables and indexes (idempotent — uses IF NOT EXISTS)."""
        logger.info("Creating DuckDB schema")
        for statement in _DDL_STATEMENTS:
            self._conn.execute(statement)
        logger.debug("Schema ready (%d statements executed)", len(_DDL_STATEMENTS))

    def is_populated(self) -> bool:
        """Return True if the compounds table has at least one row."""
        result = self._conn.execute("SELECT COUNT(*) FROM compounds").fetchone()
        populated = result is not None and result[0] > 0
        logger.debug("is_populated: %s (%d rows)", populated, result[0] if result else 0)
        return populated

    # ── Import ────────────────────────────────────────────────────────────────

    def import_from_csvs(
        self,
        lotus_metadata_path: str,
        pathways_path: str,
        superclasses_path: str,
        classes_path: str,
    ) -> None:
        """
        One-time import of the four LOTUS CSV files into DuckDB.

        Uses DuckDB's native CSV reader throughout — no Python-side materialisation.
        Each classification CSV is loaded into a TEMP TABLE, then a single SQL JOIN
        inserts all npc_classifications rows with array construction handled by DuckDB.

        Parameters
        ----------
        lotus_metadata_path:
            Path to the LOTUS metadata CSV (taxo_db_metadata).
        pathways_path:
            Path to the NPC pathways CSV (first column: SMILES).
        superclasses_path:
            Path to the NPC superclasses CSV (first column: SMILES).
        classes_path:
            Path to the NPC classes CSV (first column: SMILES).
        """
        conn: duckdb.DuckDBPyConnection = self._conn

        # -- compounds -------------------------------------------------------
        logger.info("Importing compounds from %s", lotus_metadata_path)
        t0 = time()
        conn.begin()
        try:
            conn.execute(f"""
                INSERT OR IGNORE INTO compounds
                SELECT
                    structure_smiles,
                    structure_inchikey,
                    SUBSTRING(structure_inchikey, 1, 14) AS short_inchikey,
                    structure_inchi,
                    structure_molecular_formula,
                    structure_exact_mass,
                    structure_xlogp,
                    "structure_smiles_2D",
                    structure_cid,
                    "structure_nameIupac",
                    "structure_nameTraditional",
                    structure_stereocenters_total,
                    structure_stereocenters_unspecified,
                    structure_taxonomy_classyfire_chemontid,
                    structure_taxonomy_classyfire_01kingdom,
                    structure_taxonomy_classyfire_02superclass,
                    structure_taxonomy_classyfire_03class,
                    structure_taxonomy_classyfire_04directparent,
                    structure_wikidata,
                    organism_wikidata,
                    organism_name,
                    organism_taxonomy_gbifid,
                    organism_taxonomy_ncbiid,
                    organism_taxonomy_ottid,
                    organism_taxonomy_01domain,
                    organism_taxonomy_02kingdom,
                    organism_taxonomy_03phylum,
                    organism_taxonomy_04class,
                    organism_taxonomy_05order,
                    organism_taxonomy_06family,
                    organism_taxonomy_07tribe,
                    organism_taxonomy_08genus,
                    organism_taxonomy_09species,
                    organism_taxonomy_10varietas,
                    reference_wikidata,
                    reference_doi,
                    manual_validation
                FROM read_csv('{lotus_metadata_path}',
                    nullstr=['', 'NA', 'NaN'],
                    ignore_errors=true
                )
            """)
            n_compounds = conn.execute("SELECT COUNT(*) FROM compounds").fetchone()[0]
            conn.commit()
            logger.info("Inserted %d compounds in %.2fs", n_compounds, time() - t0)
        except Exception:
            conn.rollback()
            logger.exception("Compounds import failed — transaction rolled back")
            raise

        # -- npc_classifications ---------------------------------------------
        # Load each classification CSV into a DuckDB TEMP TABLE so the engine
        # handles memory management (memory-mapped, spills to disk if needed).
        # Column names are introspected via DESCRIBE; score columns are packed
        # into FLOAT[] by list_value() inside a single INSERT...SELECT JOIN.
        # No Python-side rows, dicts, or batch loops needed.
        logger.info("Loading NPC classification CSVs into temp tables")
        t0 = time()
        conn.begin()
        try:
            for tmp_name, csv_path in (
                ("_tmp_pathways",     pathways_path),
                ("_tmp_superclasses", superclasses_path),
                ("_tmp_classes",      classes_path),
            ):
                conn.execute(f"DROP TABLE IF EXISTS {tmp_name}")
                conn.execute(f"""
                    CREATE TEMP TABLE {tmp_name} AS
                    SELECT * FROM read_csv('{csv_path}',
                        nullstr=['', 'NA', 'NaN'],
                        ignore_errors=true
                    )
                """)
                n_rows = conn.execute(f"SELECT COUNT(*) FROM {tmp_name}").fetchone()[0]
                logger.debug("Loaded %d rows into %s", n_rows, tmp_name)

            logger.info("Temp tables loaded in %.2fs", time() - t0)

            # Introspect column names: first col is the SMILES key, rest are scores.
            def _describe(tmp_name: str) -> tuple[str, list[str]]:
                """Return (smiles_col_quoted, [score_col_quoted, ...])."""
                all_cols = [f'"{r[0]}"' for r in conn.execute(f"DESCRIBE {tmp_name}").fetchall()]
                return all_cols[0], all_cols[1:]

            p_smiles, pathways_score_cols     = _describe("_tmp_pathways")
            s_smiles, superclasses_score_cols = _describe("_tmp_superclasses")
            k_smiles, classes_score_cols      = _describe("_tmp_classes")

            logger.debug(
                "Score columns — pathways: %d, superclasses: %d, classes: %d",
                len(pathways_score_cols), len(superclasses_score_cols), len(classes_score_cols),
            )
            logger.debug(
                "SMILES column names — pathways: %s, superclasses: %s, classes: %s",
                p_smiles, s_smiles, k_smiles,
            )

            # Build list_value() expressions for each classification type
            p_arr = f"list_value({', '.join(f'p.{c}' for c in pathways_score_cols)})"
            s_arr = f"list_value({', '.join(f's.{c}' for c in superclasses_score_cols)})"
            k_arr = f"list_value({', '.join(f'k.{c}' for c in classes_score_cols)})"

            # Single SQL INSERT; DuckDB joins and builds arrays natively
            logger.info("Inserting NPC classifications via SQL JOIN")
            t0 = time()
            conn.execute("DROP INDEX IF EXISTS idx_npc_classifications_smiles")
            conn.execute(f"""
                INSERT OR IGNORE INTO npc_classifications
                SELECT
                    c.structure_smiles,
                    {p_arr}::FLOAT[] AS pathways,
                    {s_arr}::FLOAT[] AS superclasses,
                    {k_arr}::FLOAT[] AS classes
                FROM compounds c
                JOIN _tmp_pathways     p ON p.{p_smiles} = c.structure_smiles
                JOIN _tmp_superclasses s ON s.{s_smiles} = c.structure_smiles
                JOIN _tmp_classes      k ON k.{k_smiles} = c.structure_smiles
            """)
            n_npc = conn.execute("SELECT COUNT(*) FROM npc_classifications").fetchone()[0]
            conn.execute(
                "CREATE INDEX idx_npc_classifications_smiles ON npc_classifications (structure_smiles)"
            )
            logger.info("Inserted %d NPC classification rows in %.2fs", n_npc, time() - t0)

            # Log compounds that had no matching NPC data
            n_skipped = n_compounds - n_npc
            if n_skipped > 0:
                logger.warning(
                    "%d compounds had no NPC classification data and were skipped", n_skipped
                )

            # Drop temp tables now that data is committed
            for tmp_name in ("_tmp_pathways", "_tmp_superclasses", "_tmp_classes"):
                conn.execute(f"DROP TABLE IF EXISTS {tmp_name}")

            # Persist column names (strip surrounding quotes for storage)
            def _strip_quotes(col: str) -> str:
                return col.strip('"')

            conn.execute("""
                CREATE TABLE IF NOT EXISTS _meta_columns (
                    table_name TEXT,
                    col_index  INTEGER,
                    col_name   TEXT,
                    PRIMARY KEY (table_name, col_index)
                )
            """)
            conn.executemany(
                "INSERT OR REPLACE INTO _meta_columns VALUES ('pathways', ?, ?)",
                [(i, _strip_quotes(c)) for i, c in enumerate(pathways_score_cols)],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO _meta_columns VALUES ('superclasses', ?, ?)",
                [(i, _strip_quotes(c)) for i, c in enumerate(superclasses_score_cols)],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO _meta_columns VALUES ('classes', ?, ?)",
                [(i, _strip_quotes(c)) for i, c in enumerate(classes_score_cols)],
            )
            logger.debug("Persisted NPC column names to _meta_columns")

            conn.commit()
            logger.info("NPC classification import committed successfully")

        except Exception:
            conn.rollback()
            logger.exception("NPC classification import failed — transaction rolled back")
            raise

    # ── Spectral library registration ──────────────────────────────────────────
    # The rows themselves are inserted by the FragHub importer
    # (loaders/spectral_libraries/fraghub.py), which owns the SELECT that reads the
    # CSV. What lives here is everything that concerns the table rather than the
    # file: id allocation, the registry row, and the bulk-load index handling.

    def next_library_id(self) -> int:
        """Return the next free ``spectral_library_registry.library_id``."""
        result = self._conn.execute(
            "SELECT MAX(library_id) FROM spectral_library_registry"
        ).fetchone()
        return (result[0] if result and result[0] is not None else -1) + 1

    def next_spectrum_id(self) -> int:
        """Return the next free ``library_spectra.id``.

        Ids are allocated from one space shared by every library, so a library can
        be imported without renumbering the ones already present.
        """
        result = self._conn.execute("SELECT MAX(id) FROM library_spectra").fetchone()
        return (result[0] if result and result[0] is not None else -1) + 1

    def get_library_by_name(self, name: str) -> Optional[dict]:
        """Return the registry row for ``name``, or None if it is not registered."""
        row = self._conn.execute(
            "SELECT * FROM spectral_library_registry WHERE name = ?", [name]
        ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._conn.description]
        return dict(zip(cols, row, strict=True))

    def delete_library(self, library_id: int) -> int:
        """Remove a library and every spectrum belonging to it.

        Spectra are deleted first: ``library_spectra.library_id`` references the
        registry, so removing the registry row while its spectra remain would leave
        the foreign key pointing at nothing.

        Returns:
            The number of spectra deleted.
        """
        deleted = self._conn.execute(
            "SELECT count(*) FROM library_spectra WHERE library_id = ?", [library_id]
        ).fetchone()[0]
        self._conn.execute(
            "DELETE FROM library_spectra WHERE library_id = ?", [library_id]
        )
        self._conn.execute(
            "DELETE FROM spectral_library_registry WHERE library_id = ?", [library_id]
        )
        logger.info("Deleted library %d and its %d spectra", library_id, deleted)
        return deleted

    def register_library(self, **fields) -> None:
        """Insert one ``spectral_library_registry`` row from column-keyed values."""
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        self._conn.execute(
            f"INSERT INTO spectral_library_registry ({columns}) VALUES ({placeholders})",
            list(fields.values()),
        )

    def drop_library_spectra_indexes(self) -> None:
        """Drop the ``library_spectra`` indexes ahead of a bulk insert."""
        for index in _LIBRARY_SPECTRA_INDEXES:
            self._conn.execute(f"DROP INDEX IF EXISTS {index}")
        logger.debug("Dropped %d spectral indexes for bulk load", len(_LIBRARY_SPECTRA_INDEXES))

    def create_library_spectra_indexes(self) -> None:
        """Recreate the ``library_spectra`` indexes after a bulk insert."""
        for statement in _DDL_STATEMENTS:
            if "ON library_spectra" in statement:
                self._conn.execute(statement)
        logger.debug("Recreated %d spectral indexes", len(_LIBRARY_SPECTRA_INDEXES))

    # ── Compound queries ───────────────────────────────────────────────────────
    # Both queries share _COMPOUND_SELECT (derived from _COMPOUND_COLUMNS) and
    # only differ in the trailing ORDER BY / WHERE clause. The projected column
    # order is the contract LotusStore uses to build Lotus objects by index.

    def get_compound_metadata_sorted_by_short_inchikey(self) -> pl.DataFrame:
        """Return all compound metadata + NPC classifications, ordered by short_inchikey."""
        logger.debug("Querying compound metadata sorted by short_inchikey")
        t0 = time()
        df = self._conn.execute(_COMPOUND_SELECT + " ORDER BY c.short_inchikey").pl()
        logger.debug(
            "get_compound_metadata_sorted_by_short_inchikey returned %d rows in %.2fs",
            len(df), time() - t0,
        )
        return df

    def get_compound_metadata_by_mass_range(self, low: float, high: float) -> pl.DataFrame:
        """Return compound metadata with structure_exact_mass in [low, high] (+ NPC classifications)."""
        logger.debug("Querying compound metadata with exact_mass in [%.4f, %.4f]", low, high)
        t0 = time()
        df = self._conn.execute(
            _COMPOUND_SELECT + " WHERE c.structure_exact_mass BETWEEN ? AND ?",
            [low, high],
        ).pl()
        logger.debug(
            "get_compound_metadata_by_mass_range returned %d rows in %.2fs", len(df), time() - t0
        )
        return df

    # ── Spectral queries ───────────────────────────────────────────────────────

    def get_registered_libraries(self) -> list[dict]:
        """Return every registered spectral library as a column-keyed dict."""
        rows = self._conn.execute(
            "SELECT library_id, name, version, ion_mode, predicted, separation, "
            "n_spectra FROM spectral_library_registry ORDER BY library_id"
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def get_candidate_spectra(
        self,
        precursor_mzs: Sequence[float],
        mode: str,
        tolerance: float,
        library_ids: Optional[Sequence[int]] = None,
    ) -> tuple[list[dict], list[tuple[int, int]]]:
        """Library rows whose precursor m/z is within ``tolerance`` of any query value.

        Answers "which library spectra could match these features?" in SQL, so only
        the candidates are materialised rather than the whole library. This is the
        same predicate as matchms' ``PrecursorMzMatch(tolerance, "Dalton")``, which
        computes ``abs(a - b) <= tol``: SQL ``BETWEEN`` is inclusive at both ends.

        Args:
            precursor_mzs: Query precursor m/z values, positionally indexed.
            mode: 'pos' or 'neg'.
            tolerance: Half-width of the window, in Daltons.
            library_ids: Restrict to these libraries; None searches every library.

        Returns:
            ``(rows, pairs)`` — the distinct candidate rows as column-keyed dicts,
            and ``(query_index, row_index)`` pairs naming which query each candidate
            was retrieved for. A candidate matching several queries appears once in
            ``rows`` and once per query in ``pairs``.
        """
        if len(precursor_mzs) == 0:
            return [], []

        t0 = time()
        # Registered as a DuckDB replacement scan: the name `queries` resolves to
        # this frame, so the join happens entirely inside the database.
        queries = pl.DataFrame(  # noqa: F841 - referenced by name in the SQL below
            {
                "query_idx": list(range(len(precursor_mzs))),
                "mz": [float(mz) for mz in precursor_mzs],
            }
        )

        library_filter = ""
        params: list = [tolerance, tolerance, mode]
        if library_ids is not None:
            placeholders = ", ".join("?" for _ in library_ids)
            library_filter = f" AND s.library_id IN ({placeholders})"
            params.extend(int(i) for i in library_ids)

        pairs_sql = (
            "SELECT q.query_idx, s.id "
            "FROM queries q JOIN library_spectra s "
            "  ON s.precursor_mz BETWEEN q.mz - ? AND q.mz + ? "
            f"WHERE s.mode = ?{library_filter}"
        )
        id_pairs = self._conn.execute(pairs_sql, params).fetchall()
        if not id_pairs:
            logger.debug("No candidates for %d queries in %.2fs",
                         len(precursor_mzs), time() - t0)
            return [], []

        # Fetch each candidate once, however many queries retrieved it.
        candidate_ids = sorted({row_id for _, row_id in id_pairs})
        placeholders = ", ".join("?" for _ in candidate_ids)
        rows = self._conn.execute(
            f"SELECT * FROM library_spectra WHERE id IN ({placeholders})",
            candidate_ids,
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        candidates = [dict(zip(cols, row, strict=True)) for row in rows]

        index_by_id = {row["id"]: i for i, row in enumerate(candidates)}
        pairs = [(query_idx, index_by_id[row_id]) for query_idx, row_id in id_pairs]

        logger.debug(
            "Retrieved %d candidates (%d pairs) for %d queries in %.2fs",
            len(candidates), len(pairs), len(precursor_mzs), time() - t0,
        )
        return candidates, pairs

    @staticmethod
    def row_to_spectrum(row: dict) -> Spectrum:
        """Build the matchms Spectrum used for cosine scoring from a candidate row.

        Only the peaks and the precursor m/z are needed: the precursor filter now
        runs in SQL, and every other library field reaches the annotation through
        the candidate row itself rather than through matchms metadata.
        """
        return Spectrum(
            mz=np.array(row["mzs"], dtype=float),
            intensities=np.array(row["intensities"], dtype=float),
            metadata={"precursor_mz": row["precursor_mz"]},
        )

    # ── Column name helpers ────────────────────────────────────────────────────

    def get_pathway_column_names(self) -> list[str]:
        """Return the original pathway column names (excluding the SMILES column)."""
        return self._get_meta_columns("pathways")

    def get_superclass_column_names(self) -> list[str]:
        """Return the original superclass column names."""
        return self._get_meta_columns("superclasses")

    def get_class_column_names(self) -> list[str]:
        """Return the original class column names."""
        return self._get_meta_columns("classes")

    def _get_meta_columns(self, table_name: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT col_name FROM _meta_columns WHERE table_name = ? ORDER BY col_index",
            [table_name],
        ).fetchall()
        return [r[0] for r in rows]

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def compound_columns(self) -> list[str]:
        """The ordered list of compound column names (matches original CSV order)."""
        return list(_COMPOUND_COLUMNS)

    def row_counts(self) -> dict[str, int]:
        """Return row counts for each table (useful for import verification)."""
        tables = [
            "compounds",
            "npc_classifications",
            "spectral_library_registry",
            "library_spectra",
        ]
        return {
            t: self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables
        }
