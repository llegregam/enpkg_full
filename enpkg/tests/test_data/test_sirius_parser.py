"""Unit tests for attaching parsed SIRIUS summaries onto the Analysis model.

Pure logic — a hand-built DataFrame stands in for the parsed
``structure_identifications_top-X.tsv``; no DuckDB, network, or SIRIUS run. Lives
under ``test_data`` (not ``test_enhancers``, which is auto-marked ``integration``)
so it runs in the fast default suite.
"""

import pandas as pd

from enpkg.monolith.enhancers.sirius_parser import (
    SiriusResults,
    attach_sirius_annotations,
    parse_sirius_version,
)


def _results(rows):
    return SiriusResults(structure_identifications_top=pd.DataFrame(rows))


def test_attach_joins_rows_to_spectra_by_feature_id(make_analysis):
    analysis = make_analysis(n_spectra=3)  # feature_ids 1, 2, 3
    results = _results([
        {"mappingFeatureId": 1, "structurePerIdRank": 2, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": "QLUPQWGVSUSFPS"},
        {"mappingFeatureId": 1, "structurePerIdRank": 1, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": "BBTZETLXNQDZKF"},
        {"mappingFeatureId": 3, "structurePerIdRank": 1, "molecularFormula": "C3H8NOP",
         "adduct": "[M + H]+", "InChIkey2D": "NQIJAUUABJHIAC"},
    ])

    attach_sirius_annotations(analysis, results)
    by_id = {spectrum.feature_id: spectrum for spectrum in analysis.spectra}

    # feature 1: two annotations, sorted by rank (1 before 2)
    assert [a.rank for a in by_id[1].sirius_annotations] == [1, 2]
    assert by_id[1].sirius_annotations[0].inchikey_2d == "BBTZETLXNQDZKF"
    # adduct whitespace is normalised to the compact form
    assert by_id[1].sirius_annotations[0].adduct == "[M+K]+"
    # feature 2: no rows -> left untouched
    assert by_id[2].sirius_annotations == []
    # feature 3: one annotation
    assert [a.molecular_formula for a in by_id[3].sirius_annotations] == ["C3H8NOP"]


def test_attach_respects_top_k(make_analysis):
    analysis = make_analysis(n_spectra=1)  # feature_id 1
    results = _results([
        {"mappingFeatureId": 1, "structurePerIdRank": rank, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": f"SKELETON{rank:06d}"}
        for rank in range(1, 6)
    ])

    attach_sirius_annotations(analysis, results, top_k=2)

    assert [a.rank for a in analysis.spectra[0].sirius_annotations] == [1, 2]


def test_attach_skips_rows_without_structure_identity(make_analysis):
    analysis = make_analysis(n_spectra=1)  # feature_id 1
    results = _results([
        {"mappingFeatureId": 1, "structurePerIdRank": 1, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": None},   # no 2D InChIKey -> skipped
        {"mappingFeatureId": 1, "structurePerIdRank": 2, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": "BBTZETLXNQDZKF"},
    ])

    attach_sirius_annotations(analysis, results)
    annotations = analysis.spectra[0].sirius_annotations

    assert len(annotations) == 1
    assert annotations[0].inchikey_2d == "BBTZETLXNQDZKF"


def test_attach_ignores_unknown_feature_ids(make_analysis):
    analysis = make_analysis(n_spectra=1)  # feature_id 1
    results = _results([
        {"mappingFeatureId": 999, "structurePerIdRank": 1, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": "BBTZETLXNQDZKF"},
    ])

    attach_sirius_annotations(analysis, results)

    assert analysis.spectra[0].sirius_annotations == []


def test_attach_with_no_summary_frame_is_a_noop(make_analysis):
    analysis = make_analysis(n_spectra=1)

    # SiriusResults() leaves structure_identifications_top as None.
    attach_sirius_annotations(analysis, SiriusResults())

    assert analysis.spectra[0].sirius_annotations == []


def test_attach_stamps_the_sirius_version_on_every_annotation(make_analysis):
    analysis = make_analysis(n_spectra=1)  # feature_id 1
    results = _results([
        {"mappingFeatureId": 1, "structurePerIdRank": rank, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": key}
        for rank, key in ((1, "BBTZETLXNQDZKF"), (2, "QLUPQWGVSUSFPS"))
    ])

    attach_sirius_annotations(analysis, results, sirius_version="6.3.4")

    assert [a.sirius_version for a in analysis.spectra[0].sirius_annotations] == ["6.3.4", "6.3.4"]


def test_attach_without_a_version_leaves_it_unset(make_analysis):
    analysis = make_analysis(n_spectra=1)
    results = _results([
        {"mappingFeatureId": 1, "structurePerIdRank": 1, "molecularFormula": "C6H9N3O3S",
         "adduct": "[M + K]+", "InChIkey2D": "BBTZETLXNQDZKF"},
    ])

    attach_sirius_annotations(analysis, results)

    assert analysis.spectra[0].sirius_annotations[0].sirius_version is None


def test_parse_sirius_version_reads_the_application_version():
    # `sirius --version` stdout, as SIRIUS 6.3.4 prints it.
    stdout = "SIRIUS 6.3.4\nSIRIUS lib: 5.7.0\nCSI:FingerID lib: 3.0.13\n"

    assert parse_sirius_version(stdout) == "6.3.4"


def test_parse_sirius_version_without_a_version_line_is_none():
    assert parse_sirius_version("Error: unknown option --version\n") is None
