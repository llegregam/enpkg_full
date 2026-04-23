"""
Persistent DuckDB wrapper for LOTUS compound metadata and spectral library.

This module provides a DatabaseManager class that:
  - Owns a file-backed DuckDB connection
  - Creates the schema (compounds, npc_classifications, spectral_library tables)
  - Imports data from the original CSV and pickle sources (one-time operation)
  - Exposes query methods that return Polars DataFrames or list[Spectrum]

All public attributes exposed by DBLoader (lotus_metadata, lotus_metadata_pathways, etc.)
are reconstructed from DuckDB queries so downstream code needs no changes.
"""

import gc
import json
import logging
import pickle
from time import time

import duckdb
import numpy as np
import polars as pl
from matchms import Spectrum

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


_DDL = """
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
);

CREATE INDEX IF NOT EXISTS idx_compounds_formula
    ON compounds (structure_molecular_formula);

CREATE INDEX IF NOT EXISTS idx_compounds_short_inchikey
    ON compounds (short_inchikey);

CREATE INDEX IF NOT EXISTS idx_compounds_exact_mass
    ON compounds (structure_exact_mass);

CREATE TABLE IF NOT EXISTS npc_classifications (
    structure_smiles TEXT PRIMARY KEY
        REFERENCES compounds (structure_smiles),
    pathways     FLOAT[],
    superclasses FLOAT[],
    classes      FLOAT[]
);

CREATE TABLE IF NOT EXISTS spectral_library (
    id             INTEGER PRIMARY KEY,
    short_inchikey TEXT    NOT NULL,
    precursor_mz   DOUBLE  NOT NULL,
    mzs            FLOAT[] NOT NULL,
    intensities    FLOAT[] NOT NULL,
    mode           TEXT    NOT NULL CHECK (mode IN ('pos', 'neg')),
    compound_name  TEXT,
    adduct         TEXT,
    charge         INTEGER,
    metadata_json  JSON
);

CREATE INDEX IF NOT EXISTS idx_spectral_precursor_mz
    ON spectral_library (precursor_mz, mode);

CREATE INDEX IF NOT EXISTS idx_spectral_short_inchikey
    ON spectral_library (short_inchikey);
"""

_SPECTRAL_IMPORT_CHUNK_SIZE = 50_000

# Columns in the compounds table that map 1-to-1 to the original CSV columns.
# Order matters: Lotus.setup_lotus_columns() will be called with this list.
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
        statements = [s.strip() for s in _DDL.split(";") if s.strip()]
        for statement in statements:
            self._conn.execute(statement)
        logger.debug("Schema ready (%d statements executed)", len(statements))

    def is_populated(self) -> bool:
        """Return True if the compounds table has at least one row."""
        result = self._conn.execute("SELECT COUNT(*) FROM compounds").fetchone()
        populated = result is not None and result[0] > 0
        logger.debug("is_populated: %s (%d rows)", populated, result[0] if result else 0)
        return populated

    # ── Import ────────────────────────────────────────────────────────────────

    def import_from_csvs(
        self,
        metadata_path: str,
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
        metadata_path:
            Path to the LOTUS metadata CSV (taxo_db_metadata).
        pathways_path:
            Path to the NPC pathways CSV (first column: SMILES).
        superclasses_path:
            Path to the NPC superclasses CSV (first column: SMILES).
        classes_path:
            Path to the NPC classes CSV (first column: SMILES).
        """
        conn = self._conn

        # -- compounds -------------------------------------------------------
        logger.info("Importing compounds from %s", metadata_path)
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
                FROM read_csv('{metadata_path}',
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

    def import_spectral_db(
        self, pkl_path: str, mode: str, chunk_size: int = _SPECTRAL_IMPORT_CHUNK_SIZE,
    ) -> None:
        """
        One-time import of a pickled list[matchms.Spectrum] into spectral_library.

        Spectra are processed in chunks and inserted via Polars DataFrames
        (DuckDB's zero-copy Arrow path) to keep memory bounded.

        Parameters
        ----------
        pkl_path:
            Path to the .pkl file containing a list[Spectrum].
        mode:
            Ionization mode, either 'pos' or 'neg'.
        chunk_size:
            Number of spectra to process per batch.
        """
        if mode not in ("pos", "neg"):
            raise ValueError(f"mode must be 'pos' or 'neg', got {mode!r}")

        logger.info("Loading spectral pickle (%s): %s", mode, pkl_path)
        t0 = time()
        with open(pkl_path, "rb") as f:
            spectra: list[Spectrum] = pickle.load(f)
        n_total = len(spectra)
        logger.info("Loaded %d spectra from pickle in %.2fs", n_total, time() - t0)

        # Determine the starting id to avoid PK conflicts if one mode was already imported
        result = self._conn.execute("SELECT MAX(id) FROM spectral_library").fetchone()
        next_id = (result[0] or -1) + 1
        logger.debug("Starting spectral_library id: %d", next_id)

        # Drop indexes for faster bulk loading
        self._conn.execute("DROP INDEX IF EXISTS idx_spectral_precursor_mz")
        self._conn.execute("DROP INDEX IF EXISTS idx_spectral_short_inchikey")

        n_inserted = 0
        n_skipped = 0
        t0 = time()

        for chunk_start in range(0, n_total, chunk_size):
            chunk_end = min(chunk_start + chunk_size, n_total)
            chunk = spectra[chunk_start:chunk_end]

            ids, short_inchikeys, precursor_mzs = [], [], []
            mzs_list, intensities_list = [], []
            modes, compound_names, adducts, charges, metadata_jsons = [], [], [], [], []

            for spec in chunk:
                precursor_mz = spec.get("precursor_mz")
                if precursor_mz is None:
                    n_skipped += 1
                    continue

                compound_name = spec.get("compound_name")
                metadata_dict = {k: v for k, v in spec.metadata.items()
                                 if k not in ("peaks_json",)}

                ids.append(next_id)
                next_id += 1
                short_inchikeys.append(compound_name or "")
                precursor_mzs.append(float(precursor_mz))
                mzs_list.append(spec.peaks.mz.tolist())
                intensities_list.append(spec.peaks.intensities.tolist())
                modes.append(mode)
                compound_names.append(compound_name)
                adducts.append(spec.get("adduct"))
                charges.append(spec.get("charge"))
                metadata_jsons.append(json.dumps(metadata_dict, default=str))

            # Free memory from the chunk early if it had no valid spectra
            if not ids:
                del chunk
                continue

            df = pl.DataFrame({
                "id":             ids,
                "short_inchikey": short_inchikeys,
                "precursor_mz":  precursor_mzs,
                "mzs":           mzs_list,
                "intensities":   intensities_list,
                "mode":          modes,
                "compound_name": compound_names,
                "adduct":        adducts,
                "charge":        charges,
                "metadata_json": metadata_jsons,
            }).cast({
                "id": pl.Int64,
                "precursor_mz": pl.Float64,
                "charge": pl.Int64,
            })

            self._conn.begin()
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO spectral_library
                       (id, short_inchikey, precursor_mz, mzs, intensities, mode,
                        compound_name, adduct, charge, metadata_json)
                    SELECT id, short_inchikey, precursor_mz, mzs, intensities, mode,
                           compound_name, adduct, charge, metadata_json
                    FROM df""",
                )
                self._conn.commit()
                n_inserted += len(df)
            except Exception:
                self._conn.rollback()
                logger.exception("Spectral import failed at chunk [%d:%d]", chunk_start, chunk_end)
                raise

            del df, ids, short_inchikeys, precursor_mzs
            del mzs_list, intensities_list
            del modes, compound_names, adducts, charges, metadata_jsons, chunk

            for idx in range(chunk_start, chunk_end):
                spectra[idx] = None
            gc.collect()

            logger.debug(
                "Chunk [%d:%d] inserted (%d rows so far, %.1fs elapsed)",
                chunk_start, chunk_end, n_inserted, time() - t0,
            )

        del spectra
        gc.collect()

        # Recreate indexes
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spectral_precursor_mz "
            "ON spectral_library (precursor_mz, mode)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spectral_short_inchikey "
            "ON spectral_library (short_inchikey)"
        )

        if n_skipped:
            logger.warning("Skipped %d spectra with no precursor_mz", n_skipped)
        logger.info(
            "Inserted %d spectra (%s) in %.2fs",
            n_inserted, mode, time() - t0,
        )

    # ── Compound queries ───────────────────────────────────────────────────────

    def get_all_compounds(self) -> pl.DataFrame:
        """
        Return all compounds joined with npc_classifications as a Polars DataFrame.

        The result includes the standard compound columns plus
        'pathways', 'superclasses', 'classes' LIST columns.
        """
        logger.debug("Querying all compounds with NPC classifications")
        t0 = time()
        df = self._conn.execute("""
            SELECT c.structure_wikidata, c.structure_inchikey, c.structure_inchi,
                   c.structure_smiles, c.structure_molecular_formula,
                   c.structure_exact_mass, c.structure_xlogp,
                   c."structure_smiles_2D", c.structure_cid,
                   c."structure_nameIupac", c."structure_nameTraditional",
                   c.structure_stereocenters_total,
                   c.structure_stereocenters_unspecified,
                   c.structure_taxonomy_classyfire_chemontid,
                   c.structure_taxonomy_classyfire_01kingdom,
                   c.structure_taxonomy_classyfire_02superclass,
                   c.structure_taxonomy_classyfire_03class,
                   c.structure_taxonomy_classyfire_04directparent,
                   c.organism_wikidata, c.organism_name,
                   c.organism_taxonomy_gbifid, c.organism_taxonomy_ncbiid,
                   c.organism_taxonomy_ottid,
                   c.organism_taxonomy_01domain, c.organism_taxonomy_02kingdom,
                   c.organism_taxonomy_03phylum, c.organism_taxonomy_04class,
                   c.organism_taxonomy_05order, c.organism_taxonomy_06family,
                   c.organism_taxonomy_07tribe, c.organism_taxonomy_08genus,
                   c.organism_taxonomy_09species, c.organism_taxonomy_10varietas,
                   c.reference_wikidata, c.reference_doi, c.manual_validation,
                   n.pathways, n.superclasses, n.classes
            FROM compounds c
            LEFT JOIN npc_classifications n USING (structure_smiles)
        """).pl()
        logger.debug("get_all_compounds returned %d rows in %.2fs", len(df), time() - t0)
        return df

    def get_compounds_sorted_by_short_inchikey(self) -> pl.DataFrame:
        """Like get_all_compounds() but ordered by short_inchikey (for Ms2Enhancer)."""
        logger.debug("Querying all compounds sorted by short_inchikey")
        t0 = time()
        df = self._conn.execute("""
            SELECT c.structure_wikidata, c.structure_inchikey, c.structure_inchi,
                   c.structure_smiles, c.structure_molecular_formula,
                   c.structure_exact_mass, c.structure_xlogp,
                   c."structure_smiles_2D", c.structure_cid,
                   c."structure_nameIupac", c."structure_nameTraditional",
                   c.structure_stereocenters_total,
                   c.structure_stereocenters_unspecified,
                   c.structure_taxonomy_classyfire_chemontid,
                   c.structure_taxonomy_classyfire_01kingdom,
                   c.structure_taxonomy_classyfire_02superclass,
                   c.structure_taxonomy_classyfire_03class,
                   c.structure_taxonomy_classyfire_04directparent,
                   c.organism_wikidata, c.organism_name,
                   c.organism_taxonomy_gbifid, c.organism_taxonomy_ncbiid,
                   c.organism_taxonomy_ottid,
                   c.organism_taxonomy_01domain, c.organism_taxonomy_02kingdom,
                   c.organism_taxonomy_03phylum, c.organism_taxonomy_04class,
                   c.organism_taxonomy_05order, c.organism_taxonomy_06family,
                   c.organism_taxonomy_07tribe, c.organism_taxonomy_08genus,
                   c.organism_taxonomy_09species, c.organism_taxonomy_10varietas,
                   c.reference_wikidata, c.reference_doi, c.manual_validation,
                   n.pathways, n.superclasses, n.classes
            FROM compounds c
            LEFT JOIN npc_classifications n USING (structure_smiles)
            ORDER BY c.short_inchikey
        """).pl()
        logger.debug(
            "get_compounds_sorted_by_short_inchikey returned %d rows in %.2fs",
            len(df), time() - t0,
        )
        return df

    def get_compounds_by_formulas(self, formulas: list[str]) -> pl.DataFrame:
        """
        Return compounds whose molecular formula is in the provided list,
        joined with npc_classifications.

        Parameters
        ----------
        formulas:
            List of molecular formula strings (e.g., ['C10H12O3', 'C15H24']).
        """
        if not formulas:
            logger.debug("get_compounds_by_formulas called with empty list — returning empty DataFrame")
            return pl.DataFrame()
        logger.debug("Querying compounds for %d molecular formulas", len(formulas))
        t0 = time()
        placeholders = ", ".join("?" * len(formulas))
        df = self._conn.execute(f"""
            SELECT c.structure_wikidata, c.structure_inchikey, c.structure_inchi,
                   c.structure_smiles, c.structure_molecular_formula,
                   c.structure_exact_mass, c.structure_xlogp,
                   c."structure_smiles_2D", c.structure_cid,
                   c."structure_nameIupac", c."structure_nameTraditional",
                   c.structure_stereocenters_total,
                   c.structure_stereocenters_unspecified,
                   c.structure_taxonomy_classyfire_chemontid,
                   c.structure_taxonomy_classyfire_01kingdom,
                   c.structure_taxonomy_classyfire_02superclass,
                   c.structure_taxonomy_classyfire_03class,
                   c.structure_taxonomy_classyfire_04directparent,
                   c.organism_wikidata, c.organism_name,
                   c.organism_taxonomy_gbifid, c.organism_taxonomy_ncbiid,
                   c.organism_taxonomy_ottid,
                   c.organism_taxonomy_01domain, c.organism_taxonomy_02kingdom,
                   c.organism_taxonomy_03phylum, c.organism_taxonomy_04class,
                   c.organism_taxonomy_05order, c.organism_taxonomy_06family,
                   c.organism_taxonomy_07tribe, c.organism_taxonomy_08genus,
                   c.organism_taxonomy_09species, c.organism_taxonomy_10varietas,
                   c.reference_wikidata, c.reference_doi, c.manual_validation,
                   n.pathways, n.superclasses, n.classes
            FROM compounds c
            LEFT JOIN npc_classifications n USING (structure_smiles)
            WHERE c.structure_molecular_formula IN ({placeholders})
        """, formulas).pl()
        logger.debug(
            "get_compounds_by_formulas returned %d rows for %d formulas in %.2fs",
            len(df), len(formulas), time() - t0,
        )
        return df

    def get_compounds_by_mass_range(self, low: float, high: float) -> pl.DataFrame:
        """Return compounds with structure_exact_mass in [low, high], joined with npc_classifications."""
        logger.debug("Querying compounds with exact_mass in [%.4f, %.4f]", low, high)
        t0 = time()
        df = self._conn.execute("""
            SELECT c.structure_wikidata, c.structure_inchikey, c.structure_inchi,
                   c.structure_smiles, c.structure_molecular_formula,
                   c.structure_exact_mass, c.structure_xlogp,
                   c."structure_smiles_2D", c.structure_cid,
                   c."structure_nameIupac", c."structure_nameTraditional",
                   c.structure_stereocenters_total,
                   c.structure_stereocenters_unspecified,
                   c.structure_taxonomy_classyfire_chemontid,
                   c.structure_taxonomy_classyfire_01kingdom,
                   c.structure_taxonomy_classyfire_02superclass,
                   c.structure_taxonomy_classyfire_03class,
                   c.structure_taxonomy_classyfire_04directparent,
                   c.organism_wikidata, c.organism_name,
                   c.organism_taxonomy_gbifid, c.organism_taxonomy_ncbiid,
                   c.organism_taxonomy_ottid,
                   c.organism_taxonomy_01domain, c.organism_taxonomy_02kingdom,
                   c.organism_taxonomy_03phylum, c.organism_taxonomy_04class,
                   c.organism_taxonomy_05order, c.organism_taxonomy_06family,
                   c.organism_taxonomy_07tribe, c.organism_taxonomy_08genus,
                   c.organism_taxonomy_09species, c.organism_taxonomy_10varietas,
                   c.reference_wikidata, c.reference_doi, c.manual_validation,
                   n.pathways, n.superclasses, n.classes
            FROM compounds c
            LEFT JOIN npc_classifications n USING (structure_smiles)
            WHERE c.structure_exact_mass BETWEEN ? AND ?
        """, [low, high]).pl()
        logger.debug(
            "get_compounds_by_mass_range returned %d rows in %.2fs", len(df), time() - t0
        )
        return df

    # ── Spectral queries ───────────────────────────────────────────────────────

    def get_spectra_by_mode(self, mode: str) -> list[Spectrum]:
        """Reconstruct all matchms.Spectrum objects for the given polarity."""
        logger.debug("Querying spectral_library for mode=%s", mode)
        t0 = time()
        rows = self._conn.execute(
            "SELECT * FROM spectral_library WHERE mode = ?", [mode]
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        spectra = self._reconstruct_spectra(rows, cols)
        logger.info("Retrieved %d spectra (mode=%s) in %.2fs", len(spectra), mode, time() - t0)
        return spectra

    def get_spectra_by_mass_range(
        self, low: float, high: float, mode: str
    ) -> list[Spectrum]:
        """Return spectra with precursor_mz in [low, high] for the given mode."""
        logger.debug(
            "Querying spectral_library: precursor_mz in [%.4f, %.4f], mode=%s", low, high, mode
        )
        t0 = time()
        rows = self._conn.execute(
            "SELECT * FROM spectral_library WHERE precursor_mz BETWEEN ? AND ? AND mode = ?",
            [low, high, mode],
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        spectra = self._reconstruct_spectra(rows, cols)
        logger.debug(
            "get_spectra_by_mass_range returned %d spectra in %.2fs", len(spectra), time() - t0
        )
        return spectra

    def _reconstruct_spectra(
        self, rows: list[tuple], cols: list[str]
    ) -> list[Spectrum]:
        """Convert raw DuckDB rows into matchms.Spectrum objects."""
        spectra = []
        for row in rows:
            r = dict(zip(cols, row))
            mzs = np.array(r["mzs"], dtype=float)
            intensities = np.array(r["intensities"], dtype=float)
            metadata: dict = {"compound_name": r["compound_name"],
                              "precursor_mz": r["precursor_mz"]}
            if r["adduct"] is not None:
                metadata["adduct"] = r["adduct"]
            if r["charge"] is not None:
                metadata["charge"] = r["charge"]
            spectra.append(Spectrum(mz=mzs, intensities=intensities, metadata=metadata))
        return spectra

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
        tables = ["compounds", "npc_classifications", "spectral_library"]
        return {
            t: self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in tables
        }
