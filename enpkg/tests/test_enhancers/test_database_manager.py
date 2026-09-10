"""Tests for the spectral-library half of DatabaseManager.

Every fixture is a small inline FragHub-shaped TSV written into ``tmp_path``, so
none of these tests needs the multi-GB export or a prebuilt database.
"""

import numpy as np
import pytest
from matchms import Spectrum, calculate_scores
from matchms.similarity import PrecursorMzMatch

from enpkg.monolith.exceptions import DatabaseError
from enpkg.monolith.loaders.database_manager import DatabaseManager
from enpkg.monolith.loaders.spectral_libraries import FragHubCsvImporter

_COLUMNS = [
    "PRECURSORMZ", "PEAKS_LIST", "MSLEVEL", "IONMODE", "INCHIKEY",
    "PRECURSORTYPE", "NAME", "SMILES", "FORMULA", "SPLASH", "PREDICTED",
    "NPCLASS_PATHWAY", "CLASSYFIRE_CLASS", "INSTRUMENT",
]


def _row(**overrides) -> list[str]:
    """One FragHub row, with sensible defaults for anything not overridden."""
    values = {
        "PRECURSORMZ": "100.0",
        "PEAKS_LIST": "178.00 0.5;75.00 1.0",
        "MSLEVEL": "2",
        "IONMODE": "positive",
        "INCHIKEY": "ABCDEFGHIJKLMN-UHFFFAOYSA-N",
        "PRECURSORTYPE": "[M+H]+",
        "NAME": "Compound",
        "SMILES": "CCO",
        "FORMULA": "C2H6O",
        "SPLASH": "splash10-aaaa",
        "PREDICTED": "false",
        "NPCLASS_PATHWAY": "Alkaloids",
        "CLASSYFIRE_CLASS": "Amines",
        "INSTRUMENT": "Orbitrap",
    }
    values.update(overrides)
    return [values[column] for column in _COLUMNS]


def _write_library(tmp_path, rows, name="POS_LC.csv", columns=_COLUMNS):
    """Write a tab-separated FragHub-shaped export and return its path."""
    path = tmp_path / name
    lines = ["\t".join(columns)] + ["\t".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def db(tmp_path):
    """An empty database with the schema created."""
    with DatabaseManager(str(tmp_path / "test.duckdb")) as manager:
        manager.create_schema()
        yield manager


def _import(db, path, name="lib", ms_level=2) -> int:
    """Register and ingest one library, returning its library_id."""
    importer = FragHubCsvImporter(path, ms_level=ms_level)
    ion_mode, predicted, _ = importer.validate(db)
    library_id = db.next_library_id()
    db.register_library(
        library_id=library_id, name=name, version="1",
        ion_mode=ion_mode, predicted=predicted,
    )
    importer.ingest(db, library_id)
    return library_id


class TestFragHubImport:
    def test_rows_are_imported_and_ms_level_filtered(self, db, tmp_path):
        path = _write_library(tmp_path, [
            _row(PRECURSORMZ="100.0"),
            _row(PRECURSORMZ="200.0"),
            _row(PRECURSORMZ="300.0", MSLEVEL="3"),
            _row(PRECURSORMZ="400.0", MSLEVEL="4"),
        ])
        _import(db, path)
        assert db.row_counts()["library_spectra"] == 2

    def test_keeping_every_ms_level(self, db, tmp_path):
        path = _write_library(tmp_path, [_row(), _row(MSLEVEL="3")])
        _import(db, path, ms_level=None)
        assert db.row_counts()["library_spectra"] == 2

    def test_short_inchikey_matches_python_slicing(self, db, tmp_path):
        inchikey = "GJWGZGXMTLYZKA-UHFFFAOYSA-N"
        path = _write_library(tmp_path, [_row(INCHIKEY=inchikey)])
        _import(db, path)
        stored = db.connection.execute(
            "SELECT short_inchikey, inchikey FROM library_spectra"
        ).fetchone()
        # Must agree with Lotus.short_inchikey, which slices in Python. DuckDB
        # slicing is 1-based inclusive, so [1:14] is the same 14 characters as
        # Python's [:14]; if these ever diverge the LOTUS join silently returns
        # nothing.
        assert stored[0] == inchikey[:14]
        assert stored[1] == inchikey

    def test_peak_lists_are_parsed_into_aligned_arrays(self, db, tmp_path):
        path = _write_library(tmp_path, [
            _row(PEAKS_LIST="51.00 0.1;52.00 0.2;53.00 0.3"),
        ])
        _import(db, path)
        mzs, intensities = db.connection.execute(
            "SELECT mzs, intensities FROM library_spectra"
        ).fetchone()
        assert len(mzs) == len(intensities) == 3
        assert mzs == pytest.approx([51.0, 52.0, 53.0])
        assert intensities == pytest.approx([0.1, 0.2, 0.3])

    @pytest.mark.parametrize("placeholder", ["NOT FOUND", "UNKNOWN", "", "N/A"])
    def test_placeholders_become_null(self, db, tmp_path, placeholder):
        path = _write_library(tmp_path, [
            _row(SMILES=placeholder, NPCLASS_PATHWAY=placeholder),
        ])
        _import(db, path)
        smiles, pathway = db.connection.execute(
            "SELECT smiles, npc_pathway FROM library_spectra"
        ).fetchone()
        assert smiles is None
        assert pathway is None

    def test_missing_smiles_is_not_an_error(self, db, tmp_path):
        # FragHub keeps a spectrum carrying InChI but no SMILES, so SMILES is
        # deliberately not part of the required column contract.
        path = _write_library(tmp_path, [_row(SMILES="NOT FOUND")])
        _import(db, path)
        assert db.row_counts()["library_spectra"] == 1

    def test_rows_without_precursor_are_skipped(self, db, tmp_path):
        path = _write_library(tmp_path, [_row(), _row(PRECURSORMZ="NOT FOUND")])
        _import(db, path)
        assert db.row_counts()["library_spectra"] == 1

    def test_ids_continue_across_libraries(self, db, tmp_path):
        first = _write_library(tmp_path, [_row(), _row()], name="a.csv")
        second = _write_library(tmp_path, [_row(), _row()], name="b.csv")
        _import(db, first, name="a")
        _import(db, second, name="b")
        ids = [row[0] for row in db.connection.execute(
            "SELECT id FROM library_spectra ORDER BY id"
        ).fetchall()]
        assert ids == [0, 1, 2, 3]

    def test_indexes_survive_an_import(self, db, tmp_path):
        path = _write_library(tmp_path, [_row()])
        _import(db, path)
        indexes = {row[0] for row in db.connection.execute(
            "SELECT index_name FROM duckdb_indexes() "
            "WHERE table_name = 'library_spectra'"
        ).fetchall()}
        assert "idx_library_spectra_precursor_mz" in indexes
        assert "idx_library_spectra_short_inchikey" in indexes


class TestColumnContract:
    def test_missing_required_column_is_rejected(self, db, tmp_path):
        columns = [c for c in _COLUMNS if c != "PRECURSORMZ"]
        rows = [[v for c, v in zip(_COLUMNS, _row(), strict=True) if c != "PRECURSORMZ"]]
        path = _write_library(tmp_path, rows, columns=columns)
        with pytest.raises(DatabaseError, match="PRECURSORMZ"):
            FragHubCsvImporter(path).validate(db)

    def test_unknown_columns_are_reported_and_preserved(self, db, tmp_path):
        columns = [*_COLUMNS, "BRAND_NEW_FIELD"]
        path = _write_library(tmp_path, [[*_row(), "keep me"]], columns=columns)
        importer = FragHubCsvImporter(path)
        _, _, unknown = importer.validate(db)
        assert unknown == ("BRAND_NEW_FIELD",)

        library_id = db.next_library_id()
        db.register_library(
            library_id=library_id, name="lib", version="1",
            ion_mode="pos", predicted=False,
        )
        importer.ingest(db, library_id)
        metadata = db.connection.execute(
            "SELECT metadata_json FROM library_spectra"
        ).fetchone()[0]
        assert "keep me" in metadata


class TestBucketHomogeneity:
    """A FragHub bucket is one ion mode and one provenance; the import checks it.

    ``ion_mode`` and ``predicted`` are stored once per library rather than per
    spectrum, so a mixed file would silently mislabel every row it contains.
    """

    def test_mixed_ion_modes_are_rejected(self, db, tmp_path):
        path = _write_library(tmp_path, [
            _row(IONMODE="positive"),
            _row(IONMODE="negative"),
        ])
        with pytest.raises(DatabaseError, match="ionization modes"):
            FragHubCsvImporter(path).validate(db)

    def test_mixed_predicted_flags_are_rejected(self, db, tmp_path):
        path = _write_library(tmp_path, [
            _row(PREDICTED="false"),
            _row(PREDICTED="true"),
        ])
        with pytest.raises(DatabaseError, match="predicted and experimental"):
            FragHubCsvImporter(path).validate(db)

    def test_ion_mode_and_provenance_come_from_the_data(self, db, tmp_path):
        # Never from the filename: FragHub names files inconsistently, so a
        # bucket called POS_LC_EXP may not describe what is inside it.
        path = _write_library(
            tmp_path,
            [_row(IONMODE="negative", PRECURSORTYPE="[M-H]-", PREDICTED="true")],
            name="POS_LC_EXP.csv",
        )
        ion_mode, predicted, _ = FragHubCsvImporter(path).validate(db)
        assert ion_mode == "neg"
        assert predicted is True


class TestCandidateRetrieval:
    def test_window_selects_only_matching_precursors(self, db, tmp_path):
        path = _write_library(tmp_path, [
            _row(PRECURSORMZ="100.000"),
            _row(PRECURSORMZ="100.005"),
            _row(PRECURSORMZ="200.000"),
        ])
        _import(db, path)
        rows, pairs = db.get_candidate_spectra([100.0], "pos", 0.01)
        assert len(rows) == 2
        assert {pair[0] for pair in pairs} == {0}

    def test_no_queries_returns_nothing(self, db, tmp_path):
        _import(db, _write_library(tmp_path, [_row()]))
        assert db.get_candidate_spectra([], "pos", 0.01) == ([], [])

    def test_other_mode_is_not_searched(self, db, tmp_path):
        _import(db, _write_library(tmp_path, [_row()]))
        rows, pairs = db.get_candidate_spectra([100.0], "neg", 0.01)
        assert rows == [] and pairs == []

    def test_library_filter_is_respected(self, db, tmp_path):
        first = _write_library(tmp_path, [_row(PRECURSORMZ="100.0")], name="a.csv")
        second = _write_library(tmp_path, [_row(PRECURSORMZ="100.0")], name="b.csv")
        id_a = _import(db, first, name="a")
        _import(db, second, name="b")
        rows, _ = db.get_candidate_spectra([100.0], "pos", 0.01, library_ids=[id_a])
        assert len(rows) == 1
        assert rows[0]["library_id"] == id_a

    def test_a_candidate_shared_by_two_queries_is_fetched_once(self, db, tmp_path):
        _import(db, _write_library(tmp_path, [_row(PRECURSORMZ="100.0")]))
        rows, pairs = db.get_candidate_spectra([99.995, 100.005], "pos", 0.01)
        assert len(rows) == 1
        assert sorted(pairs) == [(0, 0), (1, 0)]

    def test_matches_matchms_precursor_filter_exactly(self, db, tmp_path):
        """The SQL range join replaces matchms' stage-1 filter, so it must agree.

        ``PrecursorMzMatch`` computes ``abs(a - b) <= tol`` and SQL ``BETWEEN`` is
        inclusive at both ends. This test is what licenses deleting stage 1 from
        the MS2 enhancer; it should stay even though nothing else exercises
        matchms here.
        """
        rng = np.random.default_rng(20260909)
        library_mzs = rng.uniform(100.0, 900.0, 400)
        query_mzs = rng.uniform(100.0, 900.0, 60)
        tolerance = 0.05

        path = _write_library(
            tmp_path, [_row(PRECURSORMZ=f"{mz:.6f}") for mz in library_mzs]
        )
        _import(db, path)
        rows, pairs = db.get_candidate_spectra(list(query_mzs), "pos", tolerance)
        sql_pairs = {(query, rows[candidate]["id"]) for query, candidate in pairs}

        def as_spectrum(mz: float) -> Spectrum:
            return Spectrum(
                mz=np.array([50.0, 75.0]),
                intensities=np.array([0.5, 1.0]),
                metadata={"precursor_mz": float(mz)},
                metadata_harmonization=False,
            )

        scores = calculate_scores(
            references=[as_spectrum(mz) for mz in query_mzs],
            queries=[as_spectrum(mz) for mz in library_mzs],
            similarity_function=PrecursorMzMatch(tolerance, "Dalton"),
        )
        matchms_pairs = set(
            zip(scores.scores[:, :][0].tolist(), scores.scores[:, :][1].tolist(),
                strict=True)
        )
        assert sql_pairs == matchms_pairs


class TestLibraryRegistry:
    def test_registration_round_trips(self, db, tmp_path):
        library_id = _import(db, _write_library(tmp_path, [_row()]), name="FragHub")
        registered = db.get_registered_libraries()
        assert len(registered) == 1
        assert registered[0]["name"] == "FragHub"
        assert registered[0]["library_id"] == library_id
        assert db.get_library_by_name("FragHub") is not None
        assert db.get_library_by_name("absent") is None

    def test_deleting_a_library_removes_its_spectra(self, db, tmp_path):
        library_id = _import(db, _write_library(tmp_path, [_row(), _row()]))
        assert db.delete_library(library_id) == 2
        assert db.row_counts()["library_spectra"] == 0
        assert db.get_registered_libraries() == []

    def test_stats_are_updated_after_import(self, db, tmp_path):
        library_id = _import(db, _write_library(tmp_path, [_row(), _row()]))
        db.update_library_stats(library_id, n_spectra=2, n_without_inchikey=0)
        assert db.get_library_by_name("lib")["n_spectra"] == 2

    def test_spectra_cannot_reference_an_unregistered_library(self, db, tmp_path):
        # The foreign key is what forces registration to happen before ingest.
        path = _write_library(tmp_path, [_row()])
        with pytest.raises(Exception, match="[Ff]oreign key"):
            FragHubCsvImporter(path).ingest(db, library_id=99)
