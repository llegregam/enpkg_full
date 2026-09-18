"""
Ingest the LOTUS compound and NatProd-classifier tables into a DuckDB database.

Spectral libraries go into the same database through a separate step,
:mod:`enpkg.scripts.import_spectral_library`, because they are registered
individually and updated on their own schedule.

Usage
-----
python -m enpkg.scripts.import_lotus \\
    --metadata     path/to/taxo_db_metadata.csv \\
    --pathways     path/to/pathways.csv \\
    --superclasses path/to/superclasses.csv \\
    --classes      path/to/classes.csv \\
    --database     path/to/enpkg.duckdb

All four CSVs are required. The database file is created if absent; if it is already
populated the import reports its row counts and stops, so an accidental repeat cannot
rebuild a multi-gigabyte table. Pass --force to re-import anyway.

See docs/BUILDING_THE_DATABASE.md for the whole procedure.
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


def main(argv: list[str] | None = None, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Import the LOTUS compound tables into a DuckDB database.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--metadata",      required=True,  help="Path to taxo_db_metadata CSV")
    parser.add_argument("--pathways",      required=True,  help="Path to NPC pathways CSV")
    parser.add_argument("--superclasses",  required=True,  help="Path to NPC superclasses CSV")
    parser.add_argument("--classes",       required=True,  help="Path to NPC classes CSV")
    parser.add_argument("--database",      required=True,  help="Path to the .duckdb file")
    parser.add_argument("--force",         action="store_true",
                        help="Re-import even if the database already appears populated")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG-level logging (default: INFO)")
    args = parser.parse_args(argv)

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

    output_path = args.database
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
            lotus_metadata_path=args.metadata,
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

        # -- Final summary ----------------------------------------------------
        logger.info("Done. Final row counts:")
        for table, count in db.row_counts().items():
            logger.info("  %s: %d rows", table, count)
        logger.info("Database written to: %s", output_path)


if __name__ == "__main__":
    main()
