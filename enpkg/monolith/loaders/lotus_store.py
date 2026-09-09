"""DuckDB-backed, read-only source of Lotus entries and taxonomy metadata.

Single owner for compound access across enhancers. Examples: MS1 reads mass-windowed
subsets, MS2 reads the full compound set sorted by short InChIKey, Weights
reads the classification counts. Each query opens a fresh DatabaseManager
context.
"""

from logging import Logger
from time import time
from typing import Iterator, Optional

import numpy as np
import polars as pl
from tqdm.auto import tqdm

from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.exceptions import DatabaseError
from enpkg.monolith.loaders.database_manager import DatabaseManager

# _LIST_COLUMNS = ("pathways", "superclasses", "classes")


class LotusStore:
    """Single source of Lotus entries and taxonomy column metadata."""

    def __init__(self, duckdb_path: str, logger: Logger):
        if not duckdb_path:
            raise DatabaseError(
                "LotusStore requires a DuckDB path; CSV fallback is no longer supported."
            )

        self._duckdb_path = duckdb_path
        self.logger = logger

        # Get column names of each of the supergroups
        with DatabaseManager(duckdb_path, read_only=True) as db:
            if not db.is_populated():
                raise DatabaseError(
                    f"DuckDB at {duckdb_path!r} exists but compounds table is empty."
                )
            self._compound_columns: tuple[str, ...] = tuple(db.compound_columns)
            self.pathways_col_names: tuple[str, ...] = tuple(db.get_pathway_column_names())
            self.superclasses_col_names: tuple[str, ...] = tuple(db.get_superclass_column_names())
            self.classes_col_names: tuple[str, ...] = tuple(db.get_class_column_names())

        self._column_index: dict[str, int] = {
            c: i for i, c in enumerate(self._compound_columns)
        }
        self.number_of_pathways = len(self.pathways_col_names)
        self.number_of_superclasses = len(self.superclasses_col_names)
        self.number_of_classes = len(self.classes_col_names)

        self.logger.info(
            "LotusStore ready (%d compound columns, %d pathways, %d superclasses, %d classes)",
            len(self._compound_columns), self.number_of_pathways,
            self.number_of_superclasses, self.number_of_classes,
        )

        # Build canonical full-DB caches. Lazy-initialised on first call to a method
        # that needs them (all_sorted_by_short_inchikey, full_groups_by_formula,
        # or grouped_by_formula_for_mass_range). Built once per LotusStore
        # instance and shared across every consumer — MS1 and MS2 enhancers
        # see the same Lotus instances by reference, which (a) eliminates
        # duplicate Lotus instantiation between views and (b) keeps memory
        # roughly flat across batch experiments instead of growing per-
        # experiment as each enhance() call would otherwise pull and
        # materialise a fresh Lotus subset.
        #
        # Per-instance, NOT class-level: two LotusStore instances pointed at
        # different DuckDBs maintain independent caches (test contract in
        # test_lotus_store.TestColumnMappingIsolation).
        self._all_lotus_canonical: Optional[list[Lotus]] = None
        self._formula_groups: Optional[dict[str, list[Lotus]]] = None

    @property
    def compound_columns(self) -> tuple[str, ...]:
        return self._compound_columns

    def all_sorted_by_short_inchikey(self) -> list[Lotus]:
        """Return every compound as a Lotus, ordered by short_inchikey (for binary search).

        Backed by the canonical cache: returns the same list object across
        calls, sharing Lotus identity with full_groups_by_formula() and
        grouped_by_formula_for_mass_range(). Treat the returned list as
        read-only — mutating it (sort in place, append, etc.) would corrupt
        every other LotusStore view.
        """
        self._ensure_full_lotus_loaded()
        return self._all_lotus_canonical  # already sorted at build time

    def _ensure_full_lotus_loaded(self) -> None:
        """Build the canonical full Lotus list on first call; no-op afterwards.

        The canonical list is sorted by short_inchikey at build time so
        all_sorted_by_short_inchikey() can return it directly, and is the
        single source of Lotus instances for every other LotusStore view.
        """
        if self._all_lotus_canonical is not None:
            return
        self.logger.info("Building canonical Lotus list (one-time, sorted by short_inchikey)")
        start = time()
        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            df = db.get_compound_metadata_sorted_by_short_inchikey()
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        self._all_lotus_canonical = list(
            self._iter_lotus_from_df(df, desc="Creating Lotus objects")
        )
        self.logger.info(
            "Canonical Lotus list built: %d entries in %.2fs",
            len(self._all_lotus_canonical), time() - start,
        )

    def full_groups_by_formula(self) -> dict[str, list[Lotus]]:
        """Return all Lotus entries grouped by molecular formula (cached).

        Each value is a list of Lotus instances sharing the same molecular
        formula. Within a group all entries also share the same exact_mass
        because exact_mass is determined by formula, so mass-window queries
        can decide inclusion on a per-group basis (see
        grouped_by_formula_for_mass_range).

        The dict and its inner lists are built once and reused; the Lotus
        instances are the same objects returned by
        all_sorted_by_short_inchikey().
        """
        if self._formula_groups is not None:
            return self._formula_groups
        self._ensure_full_lotus_loaded()
        start = time()
        groups: dict[str, list[Lotus]] = {}
        for lotus in self._all_lotus_canonical:
            groups.setdefault(lotus.structure_molecular_formula, []).append(lotus)
        self._formula_groups = groups
        self.logger.info(
            "Built %d formula groups from canonical list in %.2fs",
            len(groups), time() - start,
        )
        return groups

    def by_mass_range(self, mz_min: float, mz_max: float) -> list[Lotus]:
        """Return Lotus entries whose exact mass is in [mz_min, mz_max]."""
        self.logger.debug(
            "Fetching compounds with exact_mass in [%.4f, %.4f]", mz_min, mz_max
        )
        start = time()
        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            df = db.get_compound_metadata_by_mass_range(mz_min, mz_max)
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        if df.is_empty():
            return []
        return list(self._iter_lotus_from_df(df, desc="Creating Lotus objects"))

    def grouped_by_formula_for_mass_range(
        self, mz_min: float, mz_max: float,
    ) -> list[list[Lotus]]:
        """Return Lotus entries in [mz_min, mz_max] grouped by molecular formula.

        Used by MS1 to build per-formula adducts. Backed by the cached
        full-DB formula groups (see full_groups_by_formula); the mass
        filter is a Python pass over the cached dict, so subsequent calls
        within a batch are essentially free compared to the first one
        (which pays the canonical-list build cost amortised across every
        consumer).

        Within a formula group every Lotus entry shares the same
        exact_mass (exact_mass is determined by molecular formula), so
        inclusion is decided once per group rather than per row. SQL
        BETWEEN is inclusive on both ends; this Python filter matches.
        """
        self.logger.debug(
            "Filtering cached formula groups for exact_mass in [%.4f, %.4f]", mz_min, mz_max
        )
        cache = self.full_groups_by_formula()
        start = time()
        groups: list[list[Lotus]] = []
        for group in cache.values():
            mass = group[0].structure_exact_mass
            if mass is None:
                # Broken row with no exact_mass — can't match any window;
                # the SQL version would also exclude it (mass IS NULL fails BETWEEN).
                continue
            if mz_min <= mass <= mz_max:
                groups.append(group)
        self.logger.debug(
            "Selected %d/%d formula groups for window in %.4fs",
            len(groups), len(cache), time() - start,
        )
        if not groups:
            self.logger.warning(
                "Mass-range query [%.4f, %.4f] returned 0 formula groups", mz_min, mz_max
            )
        return groups

    def _iter_lotus_from_df(self, df: pl.DataFrame, desc: str) -> Iterator[Lotus]:
        """Stream lotus objects from each row of a DataFrame.

        Converts DataFrame rows into Lotus objects following the column structure
        guarantee from DatabaseManager: compound columns first, then pathways,
        superclasses, and classes columns in that order.

        Args:
            df: A Polars DataFrame returned from DatabaseManager query methods
                with columns ordered as: [compound_cols..., pathways, superclasses, classes].
            desc: Description text for the progress bar.

        Yields:
            Lotus: One Lotus object per DataFrame row.
        """
        n_compound_cols = len(self._compound_columns)
        for row in tqdm(
            df.iter_rows(),
            total=len(df),
            desc=desc,
            leave=False,
            dynamic_ncols=True,
        ):
            yield self._row_to_lotus(row, n_compound_cols)

    def _row_to_lotus(self, row: tuple, n_compound_cols: int) -> Lotus:
        """Convert a DataFrame row tuple into a Lotus object.

        Assumes the row follows the DatabaseManager column order contract:
        indices [0:n_compound_cols] contain compound metadata columns,
        index n_compound_cols contains pathways data,
        index n_compound_cols+1 contains superclasses data,
        index n_compound_cols+2 contains classes data.

        Args:
            row: A tuple from DataFrame.iter_rows() containing all columns in order.
            n_compound_cols: The number of compound metadata columns (len(self._compound_columns)).

        Returns:
            Lotus: A Lotus object constructed from the row data.
        """
        pw = row[n_compound_cols]
        sc = row[n_compound_cols + 1]
        cl = row[n_compound_cols + 2]
        return Lotus.from_row(
            self._column_index,
            list(row[:n_compound_cols]),
            pathways=np.array(pw) if pw is not None else np.array([]),
            superclasses=np.array(sc) if sc is not None else np.array([]),
            classes=np.array(cl) if cl is not None else np.array([]),
        )
