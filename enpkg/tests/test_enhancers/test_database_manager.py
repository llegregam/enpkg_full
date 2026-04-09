import json
import pickle

import numpy as np
import pytest
from matchms import Spectrum

from enpkg.monolith.loaders.database_manager import DatabaseManager


def _make_spectrum(compound_name, precursor_mz, mzs, intensities, adduct=None, charge=None):
    metadata = {"compound_name": compound_name, "precursor_mz": precursor_mz}
    if adduct is not None:
        metadata["adduct"] = adduct
    if charge is not None:
        metadata["charge"] = charge
    return Spectrum(
        mz=np.array(mzs, dtype=np.float64),
        intensities=np.array(intensities, dtype=np.float64),
        metadata=metadata,
    )


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.duckdb")


@pytest.fixture()
def pkl_path(tmp_path):
    spectra = [
        _make_spectrum("ABCDEFGHIJKLMN", 100.0, [50.0, 75.0], [0.5, 1.0], adduct="[M+H]+", charge=1),
        _make_spectrum("OPQRSTUVWXYZAB", 200.0, [80.0], [0.9]),
        # This one has no precursor_mz and should be skipped
        Spectrum(
            mz=np.array([10.0], dtype=np.float64),
            intensities=np.array([0.1], dtype=np.float64),
            metadata={"compound_name": "SKIP_ME"},
        ),
        _make_spectrum("CDEFGHIJKLMNOP", 150.5, [60.0, 90.0, 110.0], [0.3, 0.7, 1.0]),
        _make_spectrum("QRSTUVWXYZABCD", 300.0, [120.0, 140.0], [0.8, 0.6], adduct="[M+Na]+", charge=1),
    ]
    path = str(tmp_path / "test_spectra.pkl")
    with open(path, "wb") as f:
        pickle.dump(spectra, f)
    return path


class TestImportSpectralDb:
    def test_basic_import(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            db.import_spectral_db(pkl_path, mode="pos", chunk_size=2)

            count = db._conn.execute("SELECT COUNT(*) FROM spectral_library").fetchone()[0]
            assert count == 4  # 5 spectra minus 1 skipped

    def test_ids_are_sequential(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            db.import_spectral_db(pkl_path, mode="pos", chunk_size=2)

            ids = [r[0] for r in db._conn.execute(
                "SELECT id FROM spectral_library ORDER BY id"
            ).fetchall()]
            assert ids == [0, 1, 2, 3]

    def test_data_correctness(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            db.import_spectral_db(pkl_path, mode="pos", chunk_size=2)

            row = db._conn.execute(
                "SELECT short_inchikey, precursor_mz, compound_name, adduct, charge, mode "
                "FROM spectral_library WHERE id = 0"
            ).fetchone()
            assert row[0] == "ABCDEFGHIJKLMN"  # short_inchikey = compound_name
            assert row[1] == pytest.approx(100.0)
            assert row[2] == "ABCDEFGHIJKLMN"
            assert row[3] == "[M+H]+"
            assert row[4] == 1
            assert row[5] == "pos"

    def test_metadata_json(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            db.import_spectral_db(pkl_path, mode="pos", chunk_size=2)

            raw = db._conn.execute(
                "SELECT metadata_json FROM spectral_library WHERE id = 0"
            ).fetchone()[0]
            meta = json.loads(raw)
            assert meta["compound_name"] == "ABCDEFGHIJKLMN"
            assert meta["precursor_mz"] == 100.0

    def test_indexes_recreated(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            db.import_spectral_db(pkl_path, mode="pos", chunk_size=2)

            indexes = [r[0] for r in db._conn.execute(
                "SELECT index_name FROM duckdb_indexes() "
                "WHERE table_name = 'spectral_library'"
            ).fetchall()]
            assert "idx_spectral_precursor_mz" in indexes
            assert "idx_spectral_short_inchikey" in indexes

    def test_two_mode_imports(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            db.import_spectral_db(pkl_path, mode="pos", chunk_size=2)
            db.import_spectral_db(pkl_path, mode="neg", chunk_size=2)

            count = db._conn.execute("SELECT COUNT(*) FROM spectral_library").fetchone()[0]
            assert count == 8  # 4 + 4

            # IDs should continue from where pos left off
            max_pos_id = db._conn.execute(
                "SELECT MAX(id) FROM spectral_library WHERE mode = 'pos'"
            ).fetchone()[0]
            min_neg_id = db._conn.execute(
                "SELECT MIN(id) FROM spectral_library WHERE mode = 'neg'"
            ).fetchone()[0]
            assert min_neg_id == max_pos_id + 1

    def test_invalid_mode_raises(self, db_path, pkl_path):
        with DatabaseManager(db_path) as db:
            db.create_schema()
            with pytest.raises(ValueError, match="mode must be"):
                db.import_spectral_db(pkl_path, mode="invalid")
