"""Unit tests for SampleMetadata (incl. the F-02 taxon/sample_type ports)."""

import io
from datetime import date, datetime

import polars as pl
import pytest

from enpkg.monolith.data.sample_metadata import SampleMetadata


def test_source_taxon_empty_markers_normalize_to_none():
    for junk in ("", "ND", "nan", "NaN"):
        assert SampleMetadata(sample_id="s", source_taxon=junk).source_taxon is None
    assert SampleMetadata(sample_id="s").has_source_taxon() is False


def test_normalized_source_taxon_strips_hybrid_and_sp_markers():
    assert SampleMetadata(sample_id="s", source_taxon="Genus sp. Foo").normalized_source_taxon == (
        "genus foo"
    )
    assert (
        SampleMetadata(sample_id="s", source_taxon="Aspalathus x linearis").normalized_source_taxon
        == "aspalathus linearis"
    )
    assert (
        SampleMetadata(sample_id="s", source_taxon="  Artemisia annua  ").normalized_source_taxon
        == "artemisia annua"
    )


def test_normalized_source_taxon_none_when_unset():
    assert SampleMetadata(sample_id="s").normalized_source_taxon is None


@pytest.mark.parametrize(
    "sample_type, expected",
    [
        (None, True),        # unknown -> treated as a sample (never dropped)
        ("sample", True),
        ("Sample", True),    # case-insensitive
        (" sample ", True),  # whitespace tolerant
        ("blank", False),
        ("QC", False),
        ("pool", False),
    ],
)
def test_is_sample(sample_type, expected):
    assert SampleMetadata(sample_id="s", sample_type=sample_type).is_sample() is expected


def test_from_dict_splits_known_and_extra_fields():
    meta = SampleMetadata.from_dict(
        {"sample_id": "s", "source_taxon": "Artemisia annua", "custom_col": "v"}
    )
    assert meta.sample_id == "s"
    assert meta.extra_fields == {"custom_col": "v"}


def test_from_dict_recognizes_sample_and_extraction_fields():
    """sample_name/collection_date/collection_location/extraction_method/
    extraction_solvent come from the user metadata file, via SampleMetadata —
    make sure they land as known fields, not extra_fields."""
    meta = SampleMetadata.from_dict(
        {
            "sample_id": "s",
            "sample_name": "VGF151_E05",
            "collection_date": "2019-03-14",
            "collection_location": "Geneva, CH",
            "extraction_method": "maceration",
            "extraction_solvent": "MeOH",
        }
    )
    assert meta.sample_name == "VGF151_E05"
    assert meta.collection_date == date(2019, 3, 14)
    assert meta.collection_location == "Geneva, CH"
    assert meta.extraction_method == "maceration"
    assert meta.extraction_solvent == "MeOH"
    assert meta.extra_fields == {}


@pytest.mark.parametrize(
    "value", [date(2019, 3, 14), datetime(2019, 3, 14, 10, 30), "2019-03-14"]
)
def test_collection_date_accepts_dates_datetimes_and_iso_strings(value):
    assert SampleMetadata(sample_id="s", collection_date=value).collection_date == date(2019, 3, 14)


def test_metadata_file_with_parsed_dates_loads():
    """The CSV reader parses date-looking columns into date objects."""
    table = "sample_id\tcollection_date\tinjection_date\nS1\t2019-03-14\t17.07.2026\n"
    row = pl.read_csv(io.StringIO(table), separator="\t", try_parse_dates=True).to_dicts()[0]
    meta = SampleMetadata.from_dict(row)
    assert meta.collection_date == date(2019, 3, 14)
    assert isinstance(meta.extra_fields["injection_date"], date)


def test_from_dict_routes_run_level_and_lineage_columns():
    """operator/instrument are known fields; the organism_* lineage columns are
    ordinary extra columns."""
    meta = SampleMetadata.from_dict(
        {
            "sample_id": "s",
            "operator": "LLG",
            "instrument": "Orbitrap Exploris 120",
            "organism_genus": "Actaea",
        }
    )
    assert meta.operator == "LLG"
    assert meta.instrument == "Orbitrap Exploris 120"
    assert meta.extra_fields == {"organism_genus": "Actaea"}
