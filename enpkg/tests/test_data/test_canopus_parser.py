"""Unit tests for attaching CANOPUS class predictions onto the Analysis model.

Pure logic — a hand-built DataFrame stands in for the parsed
``canopus_formula_summary.tsv``; no DuckDB, network, or SIRIUS run. Lives under
``test_data`` (not ``test_enhancers``, which is auto-marked ``integration``) so it runs in
the fast default suite.
"""

import pandas as pd
import pytest

from enpkg.monolith.enhancers.sirius_parser import (
    SiriusResults,
    attach_canopus_classifications,
)


def _row(feature_id, *, pathway="Terpenoids", pathway_p=0.982,
         superclass="Monoterpenoids", superclass_p=0.998,
         chemical_class="Iridoids monoterpenoids", chemical_class_p=0.944,
         formula="C18H24O13", adduct="[M + H3N + H]+", formula_rank=1):
    """One CANOPUS summary row, spelled exactly as SIRIUS writes the columns."""
    return {
        "mappingFeatureId": feature_id,
        "formulaRank": formula_rank,
        "molecularFormula": formula,
        "adduct": adduct,
        "NPC#pathway": pathway,
        "NPC#pathway Probability": pathway_p,
        "NPC#superclass": superclass,
        "NPC#superclass Probability": superclass_p,
        "NPC#class": chemical_class,
        "NPC#class Probability": chemical_class_p,
    }


def _results(rows, *, field="canopus_formula_summary"):
    return SiriusResults(**{field: pd.DataFrame(rows)})


def test_attach_joins_rows_to_spectra_by_feature_id(make_analysis):
    analysis = make_analysis(n_spectra=3)  # feature_ids 1, 2, 3
    attach_canopus_classifications(analysis, _results([_row(1), _row(3, pathway="Alkaloids")]))
    by_id = {spectrum.feature_id: spectrum for spectrum in analysis.spectra}

    assert by_id[1].has_canopus_classification()
    assert by_id[1].canopus_classification.pathway.label == "Terpenoids"
    assert by_id[1].canopus_classification.pathway.probability == pytest.approx(0.982)
    assert by_id[1].canopus_classification.chemical_class.label == "Iridoids monoterpenoids"
    # feature 2 has no CANOPUS row -> left unclassified, which is normal, not an error
    assert not by_id[2].has_canopus_classification()
    assert by_id[2].canopus_classification is None
    assert by_id[3].canopus_classification.pathway.label == "Alkaloids"


def test_adduct_whitespace_is_normalised(make_analysis):
    """Matches the compact form the serializer already emits for SIRIUS/MS1 adducts."""
    analysis = make_analysis(n_spectra=1)
    attach_canopus_classifications(analysis, _results([_row(1, adduct="[M + H3N + H]+")]))
    assert analysis.spectra[0].canopus_classification.adduct == "[M+H3N+H]+"


def test_zero_probability_rank_is_kept(make_analysis):
    """G6: 0.0 is a probability CANOPUS really emits, and it is falsy.

    Taken from real output — feature 3272 of sample 20260717_AAL_AAL_119_C10 carries
    superclass 'γ-lactam-β-lactones' at exactly 0.000. A truthiness guard anywhere on this
    path drops the triple and leaves the graph asserting a rank with no confidence.
    """
    analysis = make_analysis(n_spectra=1)
    attach_canopus_classifications(analysis, _results([
        _row(1, superclass="\u03b3-lactam-\u03b2-lactones", superclass_p=0.0),
    ]))
    superclass = analysis.spectra[0].canopus_classification.superclass
    assert superclass is not None
    assert superclass.probability == 0.0
    # and it must still be reported as a populated rank
    assert "Superclass" in dict(analysis.spectra[0].canopus_classification.ranks())


def test_missing_label_drops_only_that_rank(make_analysis):
    analysis = make_analysis(n_spectra=1)
    attach_canopus_classifications(analysis, _results([_row(1, chemical_class="", chemical_class_p="")]))
    classification = analysis.spectra[0].canopus_classification
    assert classification.chemical_class is None
    assert classification.pathway is not None
    assert [name for name, _ in classification.ranks()] == ["Pathway", "Superclass"]


def test_row_with_no_prediction_at_any_rank_is_skipped(make_analysis):
    analysis = make_analysis(n_spectra=1)
    attach_canopus_classifications(analysis, _results([
        _row(1, pathway="", pathway_p="", superclass="", superclass_p="",
             chemical_class="", chemical_class_p=""),
    ]))
    assert not analysis.spectra[0].has_canopus_classification()


def test_duplicate_features_keep_the_best_formula_rank(make_analysis):
    analysis = make_analysis(n_spectra=1)
    attach_canopus_classifications(analysis, _results([
        _row(1, formula_rank=6, pathway="Alkaloids"),
        _row(1, formula_rank=1, pathway="Terpenoids"),
    ]))
    assert analysis.spectra[0].canopus_classification.pathway.label == "Terpenoids"


def test_source_selects_the_structure_summary(make_analysis):
    """The two summaries are different data and must not be silently interchanged."""
    analysis = make_analysis(n_spectra=1)
    results = SiriusResults(
        canopus_formula_summary=pd.DataFrame([_row(1, pathway="Alkaloids")]),
        canopus_structure_summary=pd.DataFrame([_row(1, pathway="Terpenoids")]),
    )
    attach_canopus_classifications(analysis, results, source="structure")
    assert analysis.spectra[0].canopus_classification.pathway.label == "Terpenoids"


def test_unknown_source_raises(make_analysis):
    with pytest.raises(ValueError, match="Unknown CANOPUS source"):
        attach_canopus_classifications(make_analysis(), _results([_row(1)]), source="bogus")


def test_missing_column_raises(make_analysis):
    frame = pd.DataFrame([_row(1)]).drop(columns=["NPC#pathway"])
    with pytest.raises(ValueError, match=r"missing columns \['NPC#pathway'\]"):
        attach_canopus_classifications(
            make_analysis(), SiriusResults(canopus_formula_summary=frame)
        )


def test_absent_frame_is_a_no_op(make_analysis):
    analysis = make_analysis(n_spectra=1)
    attach_canopus_classifications(analysis, SiriusResults())
    assert not analysis.spectra[0].has_canopus_classification()


def test_columns_are_read_positionally_not_by_attribute(make_analysis):
    """G7: 'NPC#pathway' is not a Python identifier.

    ``itertuples()`` renames such columns to positional ``_1``/``_2``..., so attribute
    access — the style ``attach_sirius_annotations`` uses — would read the wrong column
    without raising. Extra columns in a different order must not disturb the mapping.
    """
    analysis = make_analysis(n_spectra=1)
    row = _row(1)
    reordered = {"ionMass": 466.156, **{k: row[k] for k in reversed(list(row))}, "compoundId": 7}
    attach_canopus_classifications(analysis, _results([reordered]))
    classification = analysis.spectra[0].canopus_classification
    assert classification.pathway.label == "Terpenoids"
    assert classification.superclass.label == "Monoterpenoids"
    assert classification.chemical_class.label == "Iridoids monoterpenoids"
    assert classification.molecular_formula == "C18H24O13"
