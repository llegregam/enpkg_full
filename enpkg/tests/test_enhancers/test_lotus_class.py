from unittest.mock import MagicMock

import numpy as np
import pytest

from enpkg.monolith.data.lotus_class import MAXIMAL_TAXONOMICAL_SCORE, Lotus
from enpkg.monolith.data.otl_class import Match


@pytest.fixture
def sample_columns():
    return [
        "structure_wikidata", "structure_inchikey", "structure_inchi", "structure_smiles",
        "structure_molecular_formula", "structure_exact_mass", "structure_xlogp", "structure_smiles_2D",
        "structure_cid", "structure_nameIupac", "structure_nameTraditional", "structure_stereocenters_total",
        "structure_stereocenters_unspecified", "structure_taxonomy_classyfire_chemontid",
        "structure_taxonomy_classyfire_01kingdom", "structure_taxonomy_classyfire_02superclass",
        "structure_taxonomy_classyfire_03class", "structure_taxonomy_classyfire_04directparent",
        "organism_wikidata", "organism_name", "organism_taxonomy_gbifid", "organism_taxonomy_ncbiid",
        "organism_taxonomy_ottid", "organism_taxonomy_01domain", "organism_taxonomy_02kingdom",
        "organism_taxonomy_03phylum", "organism_taxonomy_04class", "organism_taxonomy_05order",
        "organism_taxonomy_06family", "organism_taxonomy_07tribe", "organism_taxonomy_08genus",
        "organism_taxonomy_09species", "organism_taxonomy_10varietas", "reference_wikidata",
        "reference_doi", "manual_validation"
    ]


@pytest.fixture
def columns_map(sample_columns):
    # Explicit per-call column-to-index mapping replaces the old class-level
    # Lotus.setup_lotus_columns / Lotus._columns globals.
    return {column: i for i, column in enumerate(sample_columns)}


@pytest.fixture
def lotus_data_full():
    # Some sample values roughly matching the columns.
    # We replace a value with `np.nan` to test NaN conversion to None.
    series = [
        "Q123", "VNJWNFJMXRGDHO-UHFFFAOYSA-N", "InChI=1S/C...", "CC",
        "C2H6", 30.04, 1.2, "CC(C)",
        12345, "iupac name", "traditional name", 1,
        0, "CHEMONTID:123",
        "Organic", "Super",
        "Class", "Parent",
        "Q456", "Some organism", 111, 222,
        333, "Eukaryota", "Plantae",
        "Tracheophyta", "Magnoliopsida", "Asterales",
        "Asteraceae", "Anthemideae", "Artemisia",
        "Artemisia annua", np.nan, "Q789",
        "10.123/123", True
    ]

    pathways = np.array(["path1"])
    superclasses = np.array(["super1"])
    classes = np.array(["class1"])

    return series, pathways, superclasses, classes


def test_from_row(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full

    lotus_instance = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    assert lotus_instance.structure_wikidata == "Q123"
    assert lotus_instance.structure_inchikey == "VNJWNFJMXRGDHO-UHFFFAOYSA-N"
    assert lotus_instance.structure_exact_mass == 30.04
    assert lotus_instance.manual_validation is True
    # The NaN value in 'varietas' (index 32) should be converted to None
    assert lotus_instance.varietas is None


def test_from_row_with_none(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full
    # Force some values to None
    series[0] = None  # structure_wikidata

    lotus_instance = Lotus.from_row(columns_map, series, pathways, superclasses, classes)
    assert lotus_instance.structure_wikidata is None


def test_lotus_hash_and_eq(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full
    lotus1 = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    # Create identical instance
    lotus2 = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    assert lotus1 == lotus2
    assert hash(lotus1) == hash(lotus2)

    # Create a different instance (different inchikey)
    series_diff_inchi = list(series)
    series_diff_inchi[1] = "DIFFERENT-INCHIKEY"
    lotus3 = Lotus.from_row(columns_map, series_diff_inchi, pathways, superclasses, classes)

    assert lotus1 != lotus3
    assert hash(lotus1) != hash(lotus3)

    # Create a different instance (different organism)
    series_diff_org = list(series)
    series_diff_org[22] = 999  # organism_taxonomy_ottid
    lotus4 = Lotus.from_row(columns_map, series_diff_org, pathways, superclasses, classes)

    assert lotus1 != lotus4
    assert hash(lotus1) != hash(lotus4)


def test_eq_other_type(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)
    assert lotus != "a string"
    assert lotus != None  # noqa: E711 — deliberately exercises Lotus.__eq__ against None


def test_repr(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)
    repr_str = repr(lotus)
    assert "Lotus(\nTraditional name: traditional name" in repr_str
    assert "InChIKey: VNJWNFJMXRGDHO-UHFFFAOYSA-N" in repr_str
    assert "Organism: Some organism" in repr_str


def test_to_dict(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)
    lotus_dict = lotus.to_dict()

    assert isinstance(lotus_dict, dict)
    assert lotus_dict["structure_inchikey"] == "VNJWNFJMXRGDHO-UHFFFAOYSA-N"
    assert lotus_dict["organism_taxonomy_ottid"] == 333
    # Check that complex attributes are omitted, and tabular ones exist
    assert "structure_taxonomy_hammer_pathways" not in lotus_dict
    assert "structure_taxonomy_hammer_superclasses" not in lotus_dict
    assert "structure_taxonomy_hammer_classes" not in lotus_dict


def test_short_inchikey(columns_map, lotus_data_full):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    assert lotus.short_inchikey == "VNJWNFJMXRGDHO"
    assert len(lotus.short_inchikey) == 14


@pytest.fixture
def mock_match():
    match = MagicMock(spec=Match)
    match.domain = "Eukaryota"
    match.kingdom = "Plantae"
    match.phylum = "Tracheophyta"
    match.klass = "Magnoliopsida"
    match.order = "Asterales"
    match.family = "Asteraceae"
    match.genus = "Artemisia"
    match.species = "Artemisia annua"
    return match


def test_taxonomical_similarity_with_otl_match_exact(columns_map, lotus_data_full, mock_match):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    score = lotus.taxonomical_similarity_with_otl_match(mock_match)
    assert score == 8.0


@pytest.mark.parametrize("mismatches, expected_score", [
    ({"species": "Artemisia absinthium"}, 7.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis"}, 6.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis", "family": "OtherFamily"}, 5.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis", "family": "OtherFamily", "order": "OtherOrder"}, 4.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis", "family": "OtherFamily", "order": "OtherOrder", "klass": "OtherClass"}, 3.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis", "family": "OtherFamily", "order": "OtherOrder", "klass": "OtherClass", "phylum": "OtherPhylum"}, 2.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis", "family": "OtherFamily", "order": "OtherOrder", "klass": "OtherClass", "phylum": "OtherPhylum", "kingdom": "Fungi"}, 1.0),
    ({"species": "Artemisia absinthium", "genus": "Bellis", "family": "OtherFamily", "order": "OtherOrder", "klass": "OtherClass", "phylum": "OtherPhylum", "kingdom": "Fungi", "domain": "Bacteria"}, 0.0),
])
def test_taxonomical_similarity_with_otl_match_partial(columns_map, lotus_data_full, mock_match, mismatches, expected_score):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    for attr, value in mismatches.items():
        setattr(mock_match, attr, value)

    assert lotus.taxonomical_similarity_with_otl_match(mock_match) == expected_score


def test_normalized_taxonomical_similarity_with_otl_match(columns_map, lotus_data_full, mock_match):
    series, pathways, superclasses, classes = lotus_data_full
    lotus = Lotus.from_row(columns_map, series, pathways, superclasses, classes)

    score = lotus.normalized_taxonomical_similarity_with_otl_match(mock_match)
    assert score == 1.0  # 8.0 / 8.0

    mock_match.species = "Other Species"
    score_partial = lotus.normalized_taxonomical_similarity_with_otl_match(mock_match)
    assert score_partial == 7.0 / MAXIMAL_TAXONOMICAL_SCORE
