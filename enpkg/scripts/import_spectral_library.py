"""
Register a FragHub spectral-library export in a DuckDB database.

Spectral libraries must have been processed by FragHub
(https://github.com/eMetaboHUB/FragHub), which harmonises MSP, MGF, JSON, CSV and
XML from any open library into one schema. This script reads FragHub's CSV export
of a single bucket — one ion mode, one separation technique, one
generation type (experimental or predicted).

Usage
-----
python -m enpkg.scripts.import_spectral_library \\
    --database gui_workspace/databases/enpkg.duckdb \\
    --name FragHub_POS_LC_EXP --version 2024.1 \\
    --path /data/FragHub/POS_LC_EXP/POS_LC.csv \\
    --separation LC --ms-level 2

python -m enpkg.scripts.import_spectral_library --database ... --list

Ion mode and predicted/experimental status are read from the file's own IONMODE and
PREDICTED columns rather than from its name: FragHub's filenames are inconsistently
cased and do not reliably identify the bucket. Use --dry-run to validate the header
and resolve 20 rows without importing anything.
"""

import argparse
import logging
import sys
from pathlib import Path
from time import time

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    """Configure root logging so the importer's own logs are shown."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register a FragHub CSV spectral library in a DuckDB database.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--database", required=True, help="Path to the .duckdb file")
    parser.add_argument("--list", action="store_true",
                        help="List the registered libraries and exit")
    parser.add_argument("--name", help="Library name; must be unique in the database")
    parser.add_argument("--version", help="Library version, e.g. the FragHub release date")
    parser.add_argument("--path", help="Path to the FragHub CSV export")
    parser.add_argument("--separation", default=None,
                        help="Chromatography, 'LC' or 'GC'. FragHub records this only "
                             "in the bucket name, so it cannot be read from the file.")
    parser.add_argument("--fraghub-version", default=None,
                        help="Version of FragHub that produced the export")
    parser.add_argument("--ms-level", type=int, default=2,
                        help="Keep only spectra at this MS level. FragHub exports carry "
                             "MS2-MS4 in one file, and matching experimental MS2 against "
                             "library MS3/MS4 is meaningless. Use 0 to keep every level.")
    parser.add_argument("--replace", action="store_true",
                        help="Replace a library already registered under --name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and resolve 20 rows without importing")
    parser.add_argument("--require-inchikey", action="store_true",
                        help="Fail if any spectrum has no InChIKey (default: warn)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable DEBUG-level logging (default: INFO)")
    return parser


def _list_libraries(db) -> None:
    """Print the registered libraries as a table."""
    libraries = db.get_registered_libraries()
    if not libraries:
        print("No spectral library registered.")
        return
    header = f"{'id':>3}  {'name':<28} {'version':<14} {'mode':<5} {'kind':<12} {'spectra':>10}"
    print(header)
    print("-" * len(header))
    for library in libraries:
        kind = "in-silico" if library["predicted"] else "experimental"
        n_spectra = library["n_spectra"]
        print(
            f"{library['library_id']:>3}  {library['name']:<28} "
            f"{library['version']:<14} {library['ion_mode']:<5} {kind:<12} "
            f"{n_spectra if n_spectra is not None else '?':>10}"
        )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.verbose)

    # Late import so an argument error is reported before any DuckDB import error.
    from enpkg.monolith.exceptions import DatabaseError
    from enpkg.monolith.loaders.database_manager import DatabaseManager
    from enpkg.monolith.loaders.spectral_libraries import FragHubCsvImporter

    if args.list:
        with DatabaseManager(args.database) as db:
            db.create_schema()
            _list_libraries(db)
        return

    missing = [flag for flag in ("name", "version", "path") if not getattr(args, flag)]
    if missing:
        parser.error(f"--{', --'.join(missing)} required unless --list is given")

    source = Path(args.path)
    if not source.is_file():
        logger.error("Library file not found: %s", source)
        sys.exit(1)

    ms_level = args.ms_level if args.ms_level else None
    importer = FragHubCsvImporter(source, ms_level=ms_level, logger_=logger)

    # Both import scripts create the schema, so neither has to run first.
    with DatabaseManager(args.database) as db:
        db.create_schema()

        try:
            if args.dry_run:
                rows = importer.preview(db)
                logger.info("Dry run — resolved %d rows, nothing imported:", len(rows))
                for row in rows:
                    print({
                        key: value for key, value in row.items()
                        if key not in ("mzs", "intensities", "metadata_json")
                    })
                return

            existing = db.get_library_by_name(args.name)
            if existing is not None:
                if not args.replace:
                    logger.error(
                        "A library named %r is already registered (library_id=%d). "
                        "Pass --replace to overwrite it.",
                        args.name, existing["library_id"],
                    )
                    sys.exit(1)
                db.delete_library(existing["library_id"])

            # The registry row must exist before its spectra: library_spectra.
            # library_id is a foreign key into it. Ion mode and predicted status
            # come from validate(), so they are known before anything is inserted;
            # the counts are filled in afterwards.
            ion_mode, predicted, _unknown = importer.validate(db)
            library_id = db.next_library_id()
            t0 = time()
            db.register_library(
                library_id=library_id,
                name=args.name,
                version=args.version,
                ion_mode=ion_mode,
                predicted=predicted,
                separation=args.separation,
                fraghub_version=args.fraghub_version,
                source_path=str(source.resolve()),
                ms_level_filter=str(ms_level) if ms_level else None,
            )

            try:
                stats = importer.ingest(db, library_id)
                if args.require_inchikey and stats.n_without_inchikey:
                    raise DatabaseError(
                        f"{stats.n_without_inchikey} spectra have no InChIKey and "
                        "--require-inchikey was given."
                    )
            except Exception:
                # Leave no registry row describing an import that did not complete.
                db.delete_library(library_id)
                raise

            db.update_library_stats(
                library_id,
                n_spectra=stats.n_inserted,
                n_without_inchikey=stats.n_without_inchikey,
                unknown_columns=stats.unknown_columns_text,
            )
        except DatabaseError as exc:
            logger.error("%s", exc)
            sys.exit(1)

        logger.info(
            "Registered %r (library_id=%d): %d spectra, mode=%s, %s, in %.1fs",
            args.name, library_id, stats.n_inserted, stats.ion_mode,
            "in-silico" if stats.predicted else "experimental", time() - t0,
        )
        _list_libraries(db)


if __name__ == "__main__":
    main()
