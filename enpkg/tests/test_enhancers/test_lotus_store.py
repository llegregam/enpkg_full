"""Unit tests for LotusStore."""

import logging

import pytest

from enpkg.monolith.exceptions import DBLoaderError
from enpkg.monolith.loaders.database_manager import DatabaseManager
from enpkg.monolith.loaders.lotus_store import LotusStore


# Compound columns in the order DatabaseManager._COMPOUND_COLUMNS / SELECT statements use.
_COMPOUND_COLS = [
    "structure_wikidata", "structure_inchikey", "structure_inchi",
    "structure_smiles", "structure_molecular_formula",
    "structure_exact_mass", "structure_xlogp",
    "structure_smiles_2D", "structure_cid",
    "structure_nameIupac", "structure_nameTraditional",
    "structure_stereocenters_total", "structure_stereocenters_unspecified",
    "structure_taxonomy_classyfire_chemontid",
    "structure_taxonomy_classyfire_01kingdom",
    "structure_taxonomy_classyfire_02superclass",
    "structure_taxonomy_classyfire_03class",
    "structure_taxonomy_classyfire_04directparent",
    "organism_wikidata", "organism_name",
    "organism_taxonomy_gbifid", "organism_taxonomy_ncbiid",
    "organism_taxonomy_ottid",
    "organism_taxonomy_01domain", "organism_taxonomy_02kingdom",
    "organism_taxonomy_03phylum", "organism_taxonomy_04class",
    "organism_taxonomy_05order", "organism_taxonomy_06family",
    "organism_taxonomy_07tribe", "organism_taxonomy_08genus",
    "organism_taxonomy_09species", "organism_taxonomy_10varietas",
    "reference_wikidata", "reference_doi", "manual_validation",
]


def _compound(smiles: str, short_inchikey: str, formula: str, exact_mass: float) -> dict:
    """Build a minimal compounds row keyed by column name."""
    return {
        "structure_smiles": smiles,
        "structure_inchikey": short_inchikey + "-XXXXXXXXXXX-N",
        "short_inchikey": short_inchikey,
        "structure_molecular_formula": formula,
        "structure_exact_mass": exact_mass,
        "structure_wikidata": f"Q{smiles}",
        "structure_nameTraditional": f"name_{smiles}",
        "manual_validation": True,
    }


def _seed_duckdb(path: str, compounds: list[dict]) -> None:
    """Create a populated DuckDB at `path` suitable for LotusStore."""
    with DatabaseManager(path) as db:
        db.create_schema()
        conn = db._conn

        cols = [
            "structure_smiles", "structure_inchikey", "short_inchikey",
            "structure_molecular_formula", "structure_exact_mass",
            "structure_wikidata", "structure_nameTraditional", "manual_validation",
        ]
        placeholders = ", ".join(["?"] * len(cols))
        for c in compounds:
            conn.execute(
                f"INSERT INTO compounds ({', '.join(cols)}) VALUES ({placeholders})",
                [c.get(col) for col in cols],
            )

        # Populate npc_classifications with small 2-element arrays so list_get works.
        for c in compounds:
            conn.execute(
                "INSERT INTO npc_classifications (structure_smiles, pathways, superclasses, classes)"
                " VALUES (?, ?::FLOAT[], ?::FLOAT[], ?::FLOAT[])",
                [c["structure_smiles"], [0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            )

        # _meta_columns is created lazily by import_from_csvs; create it here for seeded dbs.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _meta_columns (
                table_name TEXT,
                col_index  INTEGER,
                col_name   TEXT,
                PRIMARY KEY (table_name, col_index)
            )
        """)
        for tbl, names in (
            ("pathways",     ["pw1", "pw2"]),
            ("superclasses", ["sc1", "sc2"]),
            ("classes",      ["cl1", "cl2"]),
        ):
            for i, n in enumerate(names):
                conn.execute(
                    "INSERT OR REPLACE INTO _meta_columns VALUES (?, ?, ?)",
                    [tbl, i, n],
                )


@pytest.fixture()
def logger() -> logging.Logger:
    return logging.getLogger("lotus_store_test")


@pytest.fixture()
def seeded_duckdb(tmp_path) -> str:
    path = str(tmp_path / "lotus.duckdb")
    compounds = [
        _compound("CCO",     "AAAAAAAAAAAAAA", "C2H6O",  46.0420),
        _compound("CCCC",    "BBBBBBBBBBBBBB", "C4H10",  58.0780),
        _compound("C1=CC=C", "CCCCCCCCCCCCCC", "C6H6",   78.0470),
        _compound("CN",      "DDDDDDDDDDDDDD", "CH5N",   31.0420),
        _compound("CCN",     "CCCCCCCCCCCCCC", "C2H7N",  45.0580),  # duplicate short_inchikey with C6H6 row
    ]
    _seed_duckdb(path, compounds)
    return path


class TestLotusStoreConstruction:
    def test_raises_on_empty_path(self, logger):
        with pytest.raises(DBLoaderError, match="requires a DuckDB path"):
            LotusStore(duckdb_path="", logger=logger)

    def test_raises_on_empty_db(self, tmp_path, logger):
        path = str(tmp_path / "empty.duckdb")
        with DatabaseManager(path) as db:
            db.create_schema()
        with pytest.raises(DBLoaderError, match="compounds table is empty"):
            LotusStore(duckdb_path=path, logger=logger)

    def test_metadata_populated(self, seeded_duckdb, logger):
        store = LotusStore(duckdb_path=seeded_duckdb, logger=logger)
        assert store.number_of_pathways == 2
        assert store.number_of_superclasses == 2
        assert store.number_of_classes == 2
        assert store.pathways_col_names == ("pw1", "pw2")
        assert store.superclasses_col_names == ("sc1", "sc2")
        assert store.classes_col_names == ("cl1", "cl2")
        assert tuple(store.compound_columns) == tuple(_COMPOUND_COLS)


class TestByMassRange:
    def test_returns_only_in_range(self, seeded_duckdb, logger):
        store = LotusStore(duckdb_path=seeded_duckdb, logger=logger)
        hits = store.by_mass_range(40.0, 60.0)
        formulas = sorted(h.structure_molecular_formula for h in hits)
        assert formulas == ["C2H6O", "C2H7N", "C4H10"]

    def test_empty_range_returns_empty_list(self, seeded_duckdb, logger):
        store = LotusStore(duckdb_path=seeded_duckdb, logger=logger)
        assert store.by_mass_range(1000.0, 2000.0) == []


class TestAllSortedByShortInchikey:
    def test_ordering_is_non_decreasing(self, seeded_duckdb, logger):
        store = LotusStore(duckdb_path=seeded_duckdb, logger=logger)
        lotus_objects = store.all_sorted_by_short_inchikey()
        short_keys = [l.short_inchikey for l in lotus_objects]
        assert short_keys == sorted(short_keys)
        assert len(lotus_objects) == 5


class TestGroupedByFormulaForMassRange:
    def test_groups_keyed_by_formula(self, seeded_duckdb, logger):
        store = LotusStore(duckdb_path=seeded_duckdb, logger=logger)
        groups = store.grouped_by_formula_for_mass_range(40.0, 60.0)
        group_formulas = {g[0].structure_molecular_formula for g in groups}
        assert group_formulas == {"C2H6O", "C2H7N", "C4H10"}

    def test_isomer_grouping(self, tmp_path, logger):
        """Compounds sharing a formula land in the same inner list."""
        path = str(tmp_path / "isomers.duckdb")
        compounds = [
            _compound("C1CCCCC1",  "HEXANEAAAAAAAA", "C6H12", 84.0939),
            _compound("CCCCCC",    "HEXANEBBBBBBBB", "C6H12", 84.0939),
            _compound("CCCCCCC",   "HEPTANEAAAAAAA", "C7H16", 100.1252),
        ]
        _seed_duckdb(path, compounds)
        store = LotusStore(duckdb_path=path, logger=logger)
        groups = store.grouped_by_formula_for_mass_range(80.0, 110.0)
        by_formula = {g[0].structure_molecular_formula: g for g in groups}
        assert len(by_formula["C6H12"]) == 2
        assert len(by_formula["C7H16"]) == 1


class TestColumnMappingIsolation:
    """Two LotusStore instances must not share column state via class-level globals."""

    def test_independent_stores_coexist(self, tmp_path, logger):
        path_a = str(tmp_path / "a.duckdb")
        path_b = str(tmp_path / "b.duckdb")
        _seed_duckdb(path_a, [_compound("A", "AAAA_ISOLATIONA", "CH4", 16.0313)])
        _seed_duckdb(path_b, [_compound("B", "BBBB_ISOLATIONB", "C2H4", 28.0313)])

        store_a = LotusStore(duckdb_path=path_a, logger=logger)
        store_b = LotusStore(duckdb_path=path_b, logger=logger)

        # Query each; neither should see the other's data.
        hits_a = store_a.all_sorted_by_short_inchikey()
        hits_b = store_b.all_sorted_by_short_inchikey()
        assert [h.structure_molecular_formula for h in hits_a] == ["CH4"]
        assert [h.structure_molecular_formula for h in hits_b] == ["C2H4"]

        # Column index mappings are identical in shape but are owned per-instance,
        # not shared via class-level state.
        assert store_a._column_index is not store_b._column_index
        assert store_a._column_index == store_b._column_index
