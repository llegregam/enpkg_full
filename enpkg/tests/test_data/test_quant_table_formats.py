"""Unit tests for quantification-table schema handling in AnalysisLoader.

Covers both the legacy GNPS-style MZmine export (``row ID`` / ``row m/z`` /
``row retention time`` / ``<file> Peak area``) and the newer "modular" export
(``id`` / ``mz`` / ``rt`` / ``area``), which must be normalized to the legacy
names before the rest of the loader consumes them.
"""

import polars as pl
import pytest

from enpkg.monolith.loaders.analysis_loader import AnalysisLoader

# Trimmed to the columns that matter here; a real modular export also carries
# mz_range / rt_range / fragment_scans and a per-datafile block.
MODULAR_HEADER = (
    "id,mz,rt,area,height,datafile:run.raw:area,datafile:run.raw:height"
)


def _modular_frame():
    return pl.DataFrame(
        {
            "id": [1, 2],
            "mz": [187.99893, 226.95365],
            "rt": [0.6852, 0.7599],
            "area": [5.502e3, 1.727e3],
            "height": [6.042e4, 7.555e4],
            "datafile:run.raw:area": [5.502e3, 1.727e3],
        }
    )


def test_modular_columns_renamed_to_legacy():
    result = AnalysisLoader._normalize_quant_columns(_modular_frame())
    for column in ("row ID", "row m/z", "row retention time", "Peak area"):
        assert column in result.columns
    # Values ride along with the rename rather than being recomputed.
    assert result["row ID"].to_list() == [1, 2]
    assert result["Peak area"].to_list() == [5.502e3, 1.727e3]


def test_modular_rename_prefers_area_over_height():
    """``area`` becomes the intensity column; ``height`` is left as-is.

    The two differ by an order of magnitude, so picking the wrong one silently
    changes every downstream intensity.
    """
    result = AnalysisLoader._normalize_quant_columns(_modular_frame())
    assert "Peak area" in result.columns
    assert "Peak height" not in result.columns
    assert result["height"].to_list() == [6.042e4, 7.555e4]


def test_per_datafile_columns_not_treated_as_feature_level():
    result = AnalysisLoader._normalize_quant_columns(_modular_frame())
    # The per-datafile column keeps its original name, so the intensity lookup
    # in from_files() cannot latch onto it instead of the aggregated value.
    assert "datafile:run.raw:area" in result.columns


def test_height_used_when_export_has_no_area():
    frame = pl.DataFrame({"id": [1], "mz": [100.0], "rt": [1.0], "height": [5.0]})
    result = AnalysisLoader._normalize_quant_columns(frame)
    assert "Peak height" in result.columns
    assert "Peak area" not in result.columns


def test_legacy_table_left_untouched():
    frame = pl.DataFrame(
        {
            "row ID": [1],
            "row m/z": [141.05],
            "row retention time": [0.7557],
            "sample.mzML Peak area": [13837.399],
        }
    )
    assert AnalysisLoader._normalize_quant_columns(frame).columns == frame.columns


def test_legacy_names_win_over_stray_short_columns():
    """A legacy export carrying its own ``id``/``mz`` columns is not rewritten."""
    frame = pl.DataFrame(
        {
            "row ID": [1],
            "row m/z": [141.05],
            "row retention time": [0.7557],
            "sample.mzML Peak area": [13837.399],
            "id": [99],
            "mz": [0.0],
        }
    )
    result = AnalysisLoader._normalize_quant_columns(frame)
    assert result["row ID"].to_list() == [1]
    assert result["id"].to_list() == [99]


def test_load_modular_export_from_disk(tmp_path):
    """End-to-end: a modular CSV passes the required-column check."""
    path = tmp_path / "run_quant.csv"
    path.write_text(
        f"{MODULAR_HEADER}\n"
        "1,187.99893,0.6852,5.502E3,6.042E4,5.502E3,6.042E4\n"
        "2,226.95365,0.7599,1.727E3,7.555E4,1.727E3,7.555E4\n",
        encoding="utf-8",
    )
    table = AnalysisLoader._load_quantification_table(path)
    assert table["row ID"].to_list() == [1, 2]
    # Scientific notation parses as float, not string.
    assert table["Peak area"].dtype == pl.Float64


def test_unrecognized_schema_still_raises(tmp_path):
    path = tmp_path / "bad_quant.csv"
    path.write_text("alpha,beta\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required column"):
        AnalysisLoader._load_quantification_table(path)
