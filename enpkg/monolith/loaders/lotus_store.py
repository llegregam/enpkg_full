"""DuckDB-backed, read-only source of Lotus entries and taxonomy metadata.

Single owner for compound access across enhancers. Examples: MS1 reads mass-windowed
subsets, MS2 reads the full compound set sorted by short InChIKey, Weights
reads the classification counts. Each query opens a fresh DatabaseManager
context.
"""

from logging import Logger
from time import time
from typing import Iterator

import polars as pl
import numpy as np
from tqdm.auto import tqdm

from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.exceptions import DBLoaderError
from enpkg.monolith.loaders.database_manager import DatabaseManager


_LIST_COLUMNS = ("pathways", "superclasses", "classes")


class LotusStore:
    """Single source of Lotus entries and taxonomy column metadata."""

    def __init__(self, duckdb_path: str, logger: Logger):
        if not duckdb_path:
            raise DBLoaderError(
                "LotusStore requires a DuckDB path; CSV fallback is no longer supported."
            )

        self._duckdb_path = duckdb_path
        self.logger = logger

        # Get column names of each 
        with DatabaseManager(duckdb_path, read_only=True) as db:
            if not db.is_populated():
                raise DBLoaderError(
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

    @property
    def compound_columns(self) -> tuple[str, ...]:
        return self._compound_columns

    def all_sorted_by_short_inchikey(self) -> list[Lotus]:
        """Return every compound as a Lotus, ordered by short_inchikey (for binary search)."""
        self.logger.debug("Fetching all compounds sorted by short_inchikey")
        start = time()
        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            df = db.get_compound_metadata_sorted_by_short_inchikey()
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        start = time()
        lotus_objects = list(self._iter_lotus_from_df(df, desc="Creating Lotus objects"))
        self.logger.debug(
            "Built %d Lotus objects in %.2fs", len(lotus_objects), time() - start
        )
        return lotus_objects

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

        Used by MS1 to build per-formula adducts.
        """
        self.logger.debug(
            "Fetching + grouping compounds with exact_mass in [%.4f, %.4f]", mz_min, mz_max
        )
        start = time()
        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            df = db.get_compound_metadata_by_mass_range(mz_min, mz_max)
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        if df.is_empty():
            self.logger.warning(
                "Mass-range query [%.4f, %.4f] returned 0 compounds", mz_min, mz_max
            )
            return []

        formula_col = df["structure_molecular_formula"]
        unique_formulas = formula_col.unique().to_list()
        n_compound_cols = len(self._compound_columns)

        start = time()
        groups: list[list[Lotus]] = []
        for formula in tqdm(
            unique_formulas,
            desc="Grouping Lotus by formula",
            dynamic_ncols=True,
            leave=False,
        ):
            group_df = df.filter(formula_col == formula)
            group: list[Lotus] = []
            for row in group_df.iter_rows():
                group.append(self._row_to_lotus(row, n_compound_cols))
            if group:
                groups.append(group)
        self.logger.debug(
            "Built %d formula groups in %.2fs", len(groups), time() - start
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
