"""
Build the integration-test fixtures: a small DuckDB database and a dataset
directory laid out the way ``AnalysisLoader.from_files`` expects.

The integration suite (``enpkg/tests/test_enhancers``) needs a populated
database and a real MS dataset. The production database is ~1.7 GB and the
LOTUS/ISDB source downloads are several more, which is too much to carry per
checkout. This script samples that database down to a fixture of a few tens of
megabytes and copies one dataset next to it, so the suite runs from a single
one-time command.

Sampling is deterministic: rows are ordered by ``hash()`` of their key and
truncated, so the same source database always yields the same fixture without
depending on a random seed.

Usage
-----
python -m enpkg.scripts.build_test_fixtures

Every path has a default pointing at the workspace layout; override any of them
to build from a different source. Use --force to overwrite an existing fixture.
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path
from time import time

logger = logging.getLogger(__name__)

# Repository root, derived from this file's location (enpkg/scripts/<this>).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Fixture destinations. These must match the paths the test conftest resolves
# (enpkg/tests/.databases and enpkg/tests/data), both of which are gitignored.
_TESTS_ROOT = _REPO_ROOT / "enpkg" / "tests"
_DEFAULT_OUT_DB = _TESTS_ROOT / ".databases" / "enpkg_fixture.duckdb"
_DEFAULT_OUT_DATA = _TESTS_ROOT / "data"

_DEFAULT_SOURCE_DB = _REPO_ROOT / "gui_workspace" / "databases" / "enpkg.duckdb"
_DEFAULT_BATCH_DIR = _REPO_ROOT / "gui_workspace" / "batch_input_qualome"
_DEFAULT_SAMPLE = "arnica_0_125_pos_merged"
_DEFAULT_METADATA = _DEFAULT_BATCH_DIR / "qualome_metadata.txt"

# The dataset's precursors span m/z 81-822. The window is widened well past that
# on both sides so compounds stay reachable through adduct offsets rather than
# only through their bare masses.
_MASS_MIN = 20.0
_MASS_MAX = 900.0


def _configure_logging(verbose: bool) -> None:
    """Configure root logging so DatabaseManager logs are captured alongside ours."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def build_dataset(
    batch_dir: Path, sample_name: str, metadata_path: Path, out_data: Path,
) -> Path:
    """Copy one sample's spectra and quantification table, and cut its metadata row.

    The result is laid out as ``<out_data>/<sample_name>/`` with ``msdata/processed``
    and ``metadata`` subdirectories, which is the shape the test fixtures load from.

    Args:
        batch_dir: Directory holding one subdirectory per sample.
        sample_name: Name of the sample subdirectory to copy.
        metadata_path: Batch-wide metadata table to cut the sample's row from.
        out_data: Root directory to write the dataset into.

    Returns:
        The dataset directory that was written.

    Raises:
        FileNotFoundError: The sample directory or one of its expected files is absent.
        ValueError: The metadata table has no row for this sample.
    """
    import polars as pl

    source = batch_dir / sample_name
    if not source.is_dir():
        raise FileNotFoundError(f"Sample directory not found: {source}")

    dataset_dir = out_data / sample_name
    processed = dataset_dir / "msdata" / "processed"
    metadata_dir = dataset_dir / "metadata"
    processed.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    # The SIRIUS mgf is copied alongside the others so a fixture directory is a
    # complete sample, usable by tests that reach for the SIRIUS input path.
    for suffix in (".mgf", "_quant.csv", "_sirius.mgf"):
        src_file = source / f"{sample_name}{suffix}"
        if not src_file.is_file():
            raise FileNotFoundError(f"Expected dataset file not found: {src_file}")
        shutil.copy2(src_file, processed / src_file.name)
        logger.info("Copied %s (%.1f MB)", src_file.name, src_file.stat().st_size / 1e6)

    # AnalysisLoader matches the run name against sample_filename_pos, so the row
    # is selected on that column rather than on sample_id. Every column is read as
    # a string: the table is sparse and type inference would turn empty taxonomy
    # columns into nulls of the wrong dtype.
    separator = "\t" if metadata_path.suffix in (".txt", ".tsv") else ","
    table = pl.read_csv(metadata_path, separator=separator, infer_schema_length=0)
    row = table.filter(pl.col("sample_filename_pos").str.starts_with(sample_name))
    if row.is_empty():
        raise ValueError(
            f"No metadata row whose sample_filename_pos starts with {sample_name!r} "
            f"in {metadata_path}"
        )

    out_metadata = metadata_dir / "metadata.tsv"
    row.write_csv(out_metadata, separator="\t")
    logger.info("Wrote metadata row for %s to %s", sample_name, out_metadata)
    return dataset_dir


def build_database(
    source_db: Path,
    out_db: Path,
    n_family: int,
    n_other: int,
    n_spectra: int,
    genus: str,
    family: str,
) -> None:
    """Sample the production database down to a fixture-sized copy.

    Keeps every compound of the dataset's own genus so taxonomically weighted
    scoring has real signal, then tops up with a deterministic slice of the
    surrounding family and of everything else. ``_meta_columns`` is copied whole
    because it defines the widths of the NPC probability arrays.

    Args:
        source_db: Production database to sample from (opened read-only).
        out_db: Fixture database to create.
        n_family: Number of compounds to keep from ``family`` (beyond ``genus``).
        n_other: Number of compounds to keep from outside ``family``.
        n_spectra: Number of library spectra to keep **per registered library**.
        genus: Genus whose compounds are kept in full.
        family: Family to sample the mid-tier of compounds from.
    """
    from enpkg.monolith.loaders.database_manager import DatabaseManager

    out_db.parent.mkdir(parents=True, exist_ok=True)

    with DatabaseManager(str(out_db)) as db:
        db.create_schema()
        conn = db.connection
        # ATTACH takes no bind parameters, so the path is inlined; the quote
        # doubling keeps a path containing an apostrophe from ending the literal.
        attach_path = source_db.as_posix().replace("'", "''")
        conn.execute(f"ATTACH '{attach_path}' AS src (READ_ONLY)")

        # Ordering by hash() makes the selection deterministic across runs and
        # machines without seeding DuckDB's RNG.
        mass_filter = (
            "structure_exact_mass IS NOT NULL "
            f"AND structure_exact_mass BETWEEN {_MASS_MIN} AND {_MASS_MAX}"
        )
        t0 = time()
        conn.execute(
            f"INSERT INTO compounds SELECT * FROM src.compounds "
            f"WHERE {mass_filter} AND organism_taxonomy_08genus = ?",
            [genus],
        )
        conn.execute(
            f"INSERT INTO compounds SELECT * FROM src.compounds "
            f"WHERE {mass_filter} AND organism_taxonomy_06family = ? "
            f"AND organism_taxonomy_08genus IS DISTINCT FROM ? "
            f"ORDER BY hash(structure_smiles) LIMIT {n_family}",
            [family, genus],
        )
        conn.execute(
            f"INSERT INTO compounds SELECT * FROM src.compounds "
            f"WHERE {mass_filter} AND organism_taxonomy_06family IS DISTINCT FROM ? "
            f"ORDER BY hash(structure_smiles) LIMIT {n_other}",
            [family],
        )
        n_compounds = conn.execute("SELECT count(*) FROM compounds").fetchone()[0]
        logger.info("compounds: %d rows (%.1fs)", n_compounds, time() - t0)

        # Restricted to the retained compounds: the table carries a foreign key
        # onto compounds.structure_smiles.
        t0 = time()
        conn.execute(
            "INSERT INTO npc_classifications "
            "SELECT n.* FROM src.npc_classifications n "
            "SEMI JOIN compounds c ON c.structure_smiles = n.structure_smiles"
        )
        n_npc = conn.execute("SELECT count(*) FROM npc_classifications").fetchone()[0]
        logger.info("npc_classifications: %d rows (%.1fs)", n_npc, time() - t0)

        # The registry is copied whole and first: library_spectra.library_id is a
        # foreign key onto it, so sampled spectra would otherwise reference
        # libraries that are not in the fixture.
        t0 = time()
        conn.execute(
            "INSERT INTO spectral_library_registry "
            "SELECT * FROM src.spectral_library_registry"
        )
        n_libraries = conn.execute(
            "SELECT count(*) FROM spectral_library_registry"
        ).fetchone()[0]

        # Columns are named rather than copied with SELECT *: a positional copy
        # silently misaligns the moment either schema gains a column. Sampling is
        # per library so every registered library keeps some spectra, instead of
        # one large library crowding the others out of the LIMIT.
        columns = ", ".join(
            row[0] for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'library_spectra' ORDER BY ordinal_position"
            ).fetchall()
        )
        conn.execute(
            f"INSERT INTO library_spectra ({columns}) "
            f"SELECT {columns} FROM src.library_spectra "
            f"WHERE precursor_mz BETWEEN {_MASS_MIN} AND {_MASS_MAX} "
            f"QUALIFY row_number() OVER "
            f"    (PARTITION BY library_id ORDER BY hash(id)) <= {n_spectra}"
        )
        n_spec = conn.execute("SELECT count(*) FROM library_spectra").fetchone()[0]
        # Keep the registry's counts describing the fixture, not the source.
        conn.execute(
            "UPDATE spectral_library_registry r SET n_spectra = ("
            "  SELECT count(*) FROM library_spectra s WHERE s.library_id = r.library_id"
            ")"
        )
        logger.info(
            "spectral_library_registry: %d rows; library_spectra: %d rows (%.1fs)",
            n_libraries, n_spec, time() - t0,
        )

        # Copied whole: these rows name the columns behind the pathway,
        # superclass and class probability arrays, so a partial copy would
        # silently misalign every score. The table is created here rather than
        # by create_schema(), which does not define it — in a production build
        # it is derived from the headers of the NPC classification CSVs.
        conn.execute("CREATE TABLE _meta_columns AS SELECT * FROM src._meta_columns")
        n_meta = conn.execute("SELECT count(*) FROM _meta_columns").fetchone()[0]
        logger.info("_meta_columns: %d rows", n_meta)

        conn.execute("DETACH src")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the integration-test database and dataset fixtures.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source-db", type=Path, default=_DEFAULT_SOURCE_DB,
                        help="Production DuckDB database to sample from")
    parser.add_argument("--batch-dir", type=Path, default=_DEFAULT_BATCH_DIR,
                        help="Directory holding one subdirectory per sample")
    parser.add_argument("--sample-name", default=_DEFAULT_SAMPLE,
                        help="Name of the sample directory to turn into the fixture")
    parser.add_argument("--metadata", type=Path, default=_DEFAULT_METADATA,
                        help="Batch metadata table to cut the sample's row from")
    parser.add_argument("--out-db", type=Path, default=_DEFAULT_OUT_DB,
                        help="Fixture database to write")
    parser.add_argument("--out-data", type=Path, default=_DEFAULT_OUT_DATA,
                        help="Directory to write the fixture dataset into")
    parser.add_argument("--genus", default="Arnica",
                        help="Genus whose compounds are kept in full")
    parser.add_argument("--family", default="Asteraceae",
                        help="Family to sample the mid-tier of compounds from")
    parser.add_argument("--n-family", type=int, default=5000,
                        help="Compounds to keep from --family")
    parser.add_argument("--n-other", type=int, default=5000,
                        help="Compounds to keep from outside --family")
    parser.add_argument("--n-spectra", type=int, default=10000,
                        help="Spectral-library entries to keep")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing fixture database")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG-level logging (default: INFO)")
    args = parser.parse_args()

    _configure_logging(args.verbose)

    if not args.source_db.is_file():
        logger.error("Source database not found: %s", args.source_db)
        sys.exit(1)

    if args.out_db.exists():
        if not args.force:
            logger.error("%s already exists. Use --force to rebuild.", args.out_db)
            sys.exit(1)
        args.out_db.unlink()
        logger.info("Removed existing fixture database %s", args.out_db)

    try:
        dataset_dir = build_dataset(
            args.batch_dir, args.sample_name, args.metadata, args.out_data,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        sys.exit(1)

    build_database(
        args.source_db, args.out_db,
        n_family=args.n_family, n_other=args.n_other, n_spectra=args.n_spectra,
        genus=args.genus, family=args.family,
    )

    logger.info("Dataset  : %s", dataset_dir)
    logger.info("Database : %s (%.1f MB)", args.out_db, args.out_db.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
