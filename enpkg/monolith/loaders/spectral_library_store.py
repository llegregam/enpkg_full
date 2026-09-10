"""DuckDB-backed, read-only source of spectral-library entries.

Single owner for spectral-library access, mirroring :class:`LotusStore`: built from
a DuckDB path, opening a fresh read-only ``DatabaseManager`` per query rather than
holding a connection open.

Retrieval is bounded by precursor m/z rather than by library. Asking the database
which spectra could match a set of features, instead of loading every spectrum and
filtering in Python, keeps memory proportional to the number of candidates rather
than to the size of the library — the difference between ~100 MB and several GB on
a library the size of a FragHub export.
"""

from dataclasses import dataclass
from logging import Logger
from typing import Optional, Sequence

from matchms import Spectrum

from enpkg.monolith.exceptions import DatabaseError
from enpkg.monolith.loaders.database_manager import DatabaseManager


@dataclass(frozen=True, slots=True)
class LibraryInfo:
    """One row of ``spectral_library_registry``."""

    library_id: int
    name: str
    version: str
    ion_mode: str
    predicted: bool
    separation: Optional[str]
    n_spectra: Optional[int]

    @property
    def label(self) -> str:
        """``name:version`` — what an annotation records as the library it matched."""
        return f"{self.name}:{self.version}"


@dataclass(frozen=True, slots=True)
class LibraryCandidate:
    """One library spectrum retrieved as a possible match for a feature.

    Carries the library's own assertions about the structure as typed fields rather
    than inside matchms metadata, so building an annotation needs no lookup back
    into the database and no metadata parsing.
    """

    spectrum: Spectrum
    library_id: int
    inchikey: Optional[str]
    short_inchikey: Optional[str]
    smiles: Optional[str]
    molecular_formula: Optional[str]
    compound_name: Optional[str]
    adduct: Optional[str]
    splash: Optional[str]
    npc_pathway: Optional[str]
    npc_superclass: Optional[str]
    npc_class: Optional[str]
    classyfire_superclass: Optional[str]
    classyfire_class: Optional[str]
    classyfire_subclass: Optional[str]

    @classmethod
    def from_row(cls, row: dict) -> "LibraryCandidate":
        """Build a candidate from a ``library_spectra`` row dict."""
        return cls(
            spectrum=DatabaseManager.row_to_spectrum(row),
            library_id=row["library_id"],
            inchikey=row["inchikey"],
            short_inchikey=row["short_inchikey"],
            smiles=row["smiles"],
            molecular_formula=row["molecular_formula"],
            compound_name=row["compound_name"],
            adduct=row["adduct"],
            splash=row["splash"],
            npc_pathway=row["npc_pathway"],
            npc_superclass=row["npc_superclass"],
            npc_class=row["npc_class"],
            classyfire_superclass=row["classyfire_superclass"],
            classyfire_class=row["classyfire_class"],
            classyfire_subclass=row["classyfire_subclass"],
        )


class SpectralLibraryStore:
    """Read-only access to the spectral libraries registered in a DuckDB file."""

    def __init__(
        self,
        duckdb_path: str,
        logger: Logger,
        *,
        library_names: Optional[Sequence[str]] = None,
    ) -> None:
        """Open the store and resolve which registered libraries it exposes.

        Args:
            duckdb_path: Path to the database built by the ``import_*`` scripts.
            logger: Runtime logger.
            library_names: Restrict the store to these library names; ``None``
                exposes every registered library.

        Raises:
            DatabaseError: If no path is given, if the database has no registered
                library, or if a requested name is not registered.
        """
        if not duckdb_path:
            raise DatabaseError("SpectralLibraryStore requires a DuckDB path.")

        self._duckdb_path = duckdb_path
        self.logger = logger

        with DatabaseManager(duckdb_path, read_only=True) as db:
            registered = [LibraryInfo(**row) for row in db.get_registered_libraries()]

        if not registered:
            raise DatabaseError(
                f"DuckDB at {duckdb_path!r} has no registered spectral library. "
                "Register one with `python -m enpkg.scripts.import_spectral_library`."
            )

        self._libraries = self._select(registered, library_names)
        self.logger.info(
            "SpectralLibraryStore ready (%d of %d registered librar%s: %s)",
            len(self._libraries), len(registered),
            "y" if len(self._libraries) == 1 else "ies",
            ", ".join(library.label for library in self._libraries),
        )

    @staticmethod
    def _select(
        registered: Sequence[LibraryInfo],
        library_names: Optional[Sequence[str]],
    ) -> tuple[LibraryInfo, ...]:
        """Filter the registry to ``library_names``, or return all of it.

        An unknown name is an error rather than an empty result: it is almost
        always a typo in the config, and silently querying nothing would surface
        much later as "no MS2 annotations" with nothing to indicate why.
        """
        if not library_names:
            return tuple(registered)

        by_name = {library.name: library for library in registered}
        unknown = [name for name in library_names if name not in by_name]
        if unknown:
            raise DatabaseError(
                f"Unknown spectral librar{'y' if len(unknown) == 1 else 'ies'}: "
                f"{', '.join(repr(name) for name in unknown)}. Registered: "
                f"{', '.join(sorted(by_name))}."
            )
        return tuple(by_name[name] for name in library_names)

    @property
    def libraries(self) -> tuple[LibraryInfo, ...]:
        """The registered libraries this store exposes."""
        return self._libraries

    def libraries_for_mode(self, mode: str) -> tuple[LibraryInfo, ...]:
        """The exposed libraries whose ion mode is ``mode`` ('pos' or 'neg')."""
        return tuple(library for library in self._libraries if library.ion_mode == mode)

    def candidates_for(
        self,
        precursor_mzs: Sequence[float],
        mode: str,
        tolerance: float,
    ) -> tuple[list[LibraryCandidate], list[tuple[int, int]]]:
        """Library spectra whose precursor m/z is within ``tolerance`` of a query.

        Args:
            precursor_mzs: Query precursor m/z values, positionally indexed.
            mode: 'pos' or 'neg'; only libraries of that mode are searched.
            tolerance: Half-width of the window, in Daltons.

        Returns:
            ``(candidates, pairs)`` where each pair is
            ``(index into precursor_mzs, index into candidates)``. A candidate
            matching several queries appears once in ``candidates`` and once per
            query in ``pairs``.
        """
        libraries = self.libraries_for_mode(mode)
        if not libraries:
            self.logger.warning(
                "No registered spectral library for ionization mode %r; "
                "MS2 matching will find nothing.", mode,
            )
            return [], []

        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            rows, pairs = db.get_candidate_spectra(
                precursor_mzs,
                mode,
                tolerance,
                library_ids=[library.library_id for library in libraries],
            )
        return [LibraryCandidate.from_row(row) for row in rows], pairs
