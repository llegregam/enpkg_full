"""DuckDB-backed, read-only source of Lotus entries and taxonomy metadata.

Single owner for compound access across enhancers. Examples: MS1 reads mass-windowed
subsets, MS2 reads the full compound set sorted by short InChIKey, Weights
reads the classification counts. Each query opens a fresh DatabaseManager
context.
"""

from logging import Logger
from time import time
from typing import Iterator

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
            df = db.get_compounds_sorted_by_short_inchikey()
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        start = time()
        lotus_objects = list(self._iter_lotus_from_df(df, desc="Creating Lotus objects"))
        self.logger.debug(
            "Built %d Lotus objects in %.2fs", len(lotus_objects), time() - start
        )
        return lotus_objects

    def by_mass_range(self, em_min: float, em_max: float) -> list[Lotus]:
        """Return Lotus entries whose exact mass is in [em_min, em_max]."""
        self.logger.debug(
            "Fetching compounds with exact_mass in [%.4f, %.4f]", em_min, em_max
        )
        start = time()
        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            df = db.get_compounds_by_mass_range(em_min, em_max)
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        if df.is_empty():
            return []
        return list(self._iter_lotus_from_df(df, desc="Creating Lotus objects"))

    def grouped_by_formula_for_mass_range(
        self, em_min: float, em_max: float,
    ) -> list[list[Lotus]]:
        """Return Lotus entries in [em_min, em_max] grouped by molecular formula.

        Used by MS1 to build per-formula adducts.
        """
        self.logger.debug(
            "Fetching + grouping compounds with exact_mass in [%.4f, %.4f]", em_min, em_max
        )
        start = time()
        with DatabaseManager(self._duckdb_path, read_only=True) as db:
            df = db.get_compounds_by_mass_range(em_min, em_max)
        self.logger.debug(
            "DuckDB returned %d compounds in %.2fs", len(df), time() - start
        )
        if df.is_empty():
            self.logger.warning(
                "Mass-range query [%.4f, %.4f] returned 0 compounds", em_min, em_max
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

    def _iter_lotus_from_df(self, df, desc: str) -> Iterator[Lotus]:
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
