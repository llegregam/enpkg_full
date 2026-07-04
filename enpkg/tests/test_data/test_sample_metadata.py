"""Unit tests for SampleMetadata (incl. the F-02 taxon/sample_type ports)."""

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
