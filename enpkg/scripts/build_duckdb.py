"""
One-time script to ingest LOTUS CSV files and spectral pickle files into a
persistent DuckDB database file.

Usage
-----
python -m enpkg.scripts.build_duckdb \\
    --metadata    path/to/taxo_db_metadata.csv \\
    --pathways    path/to/pathways.csv \\
    --superclasses path/to/superclasses.csv \\
    --classes     path/to/classes.csv \\
    --spectral-pos path/to/isdb_pos.pkl \\
    --spectral-neg path/to/isdb_neg.pkl \\
    --output      path/to/enpkg.duckdb

All spectral arguments are optional — omit any you don't have.
Use --force to re-import into an existing database.
"""

import argparse
import logging
import sys
from pathlib import Path
from time import time


def _configure_logging(verbose: bool) -> None:
    """Configure root logging so that DatabaseManager logs are captured.

    - INFO level by default, DEBUG when --verbose is passed.
    - Format: timestamp  level  logger  message
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a DuckDB database from LOTUS CSVs and spectral pickles.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--metadata",      required=True,  help="Path to taxo_db_metadata CSV")
    parser.add_argument("--pathways",      required=True,  help="Path to NPC pathways CSV")
    parser.add_argument("--superclasses",  required=True,  help="Path to NPC superclasses CSV")
    parser.add_argument("--classes",       required=True,  help="Path to NPC classes CSV")
    parser.add_argument("--spectral-pos",  default=None,   help="Path to positive-mode spectral pickle")
    parser.add_argument("--spectral-neg",  default=None,   help="Path to negative-mode spectral pickle")
    parser.add_argument("--output",        required=True,  help="Output .duckdb file path")
    parser.add_argument("--force",         action="store_true",
                        help="Re-import even if the database already appears populated")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG-level logging (default: INFO)")
    args = parser.parse_args()

    _configure_logging(args.verbose)

    # Validate input paths
    required_paths = {
        "metadata":     args.metadata,
        "pathways":     args.pathways,
        "superclasses": args.superclasses,
        "classes":      args.classes,
    }
    for name, path in required_paths.items():
        if not Path(path).is_file():
            logger.error("%s file not found: %s", name, path)
            sys.exit(1)

    # Late import so the error message above is shown before any DuckDB import error
    try:
        from enpkg.monolith.loaders.database_manager import DatabaseManager
    except ImportError as e:
        logger.error("Could not import DatabaseManager: %s", e)
        logger.error("Install duckdb with: pip install duckdb")
        sys.exit(1)

    output_path = args.output
    logger.info("Opening database: %s", output_path)
    with DatabaseManager(output_path) as db:
        db.create_schema()

        if db.is_populated() and not args.force:
            logger.info("Database already populated. Use --force to re-import.")
            counts = db.row_counts()
            for table, count in counts.items():
                logger.info("  %s: %d rows", table, count)
            return

        # -- Compounds + NPC classifications ----------------------------------
        logger.info("Importing LOTUS metadata and NPC classifications")
        t0 = time()
        db.import_from_csvs(
            metadata_path=args.metadata,
            pathways_path=args.pathways,
            superclasses_path=args.superclasses,
            classes_path=args.classes,
        )
        elapsed = time() - t0
        counts = db.row_counts()
        logger.info(
            "compounds: %d rows, npc_classifications: %d rows (%.1fs total)",
            counts["compounds"], counts["npc_classifications"], elapsed,
        )

        # -- Spectral databases -----------------------------------------------
        for mode, pkl_path in [("pos", args.spectral_pos), ("neg", args.spectral_neg)]:
            if pkl_path is None:
                continue
            if not Path(pkl_path).is_file():
                logger.warning("spectral-%s file not found: %s — skipping", mode, pkl_path)
                continue
            logger.info("Importing %s spectral library: %s", mode, pkl_path)
            t0 = time()
            db.import_spectral_db(pkl_path=pkl_path, mode=mode)
            elapsed = time() - t0
            count = db.row_counts()["spectral_library"]
            logger.info("spectral_library (%s): %d rows total (%.1fs)", mode, count, elapsed)

        # -- Final summary ----------------------------------------------------
        logger.info("Done. Final row counts:")
        for table, count in db.row_counts().items():
            logger.info("  %s: %d rows", table, count)
        logger.info("Database written to: %s", output_path)


if __name__ == "__main__":
    main()
