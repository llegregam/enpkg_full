"""Unit tests for the deterministic RDF URI minting."""

from rdflib import URIRef

from enpkg.monolith.data.chemical_annotation import AnnotationOrganism
from enpkg.monolith.data.ms1_data_classes.adduct_class import AdductRecipe
from enpkg.monolith.rdf.namespaces import EMI_RES, INCHIKEY
from enpkg.monolith.rdf.uris import (
    AnalysisURIs,
    CompoundURIs,
    OrganismURIs,
    _formula_segment,
    _organism_uri,
    _recipe_hash,
    _short_hash,
)


def _organism(**overrides):
    base = dict(
        name="x", wikidata=None, ott_id=None, domain=None, kingdom=None,
        phylum=None, klass=None, order=None, family=None, genus=None, species=None,
    )
    return AnnotationOrganism(**{**base, **overrides})


def test_short_hash_is_deterministic_and_process_independent():
    assert _short_hash("abc") == _short_hash("abc")
    assert len(_short_hash("abc")) == 16  # blake2b digest_size=8 bytes
    assert _short_hash("abc") != _short_hash("abd")


def test_recipe_hash_is_ingredient_order_independent():
    r1 = AdductRecipe(ingredients={"proton": 1, "sodium": 1}, charge=1, positive=True)
    r2 = AdductRecipe(ingredients={"sodium": 1, "proton": 1}, charge=1, positive=True)
    assert _recipe_hash(r1) == _recipe_hash(r2)


def test_recipe_hash_distinguishes_charge():
    r1 = AdductRecipe(ingredients={"proton": 1}, charge=1, positive=True)
    r2 = AdductRecipe(ingredients={"proton": 1}, charge=2, positive=True)
    assert _recipe_hash(r1) != _recipe_hash(r2)


def test_analysis_scoped_uris(make_analysis):
    analysis = make_analysis(run_name="RUNX")
    assert AnalysisURIs.analysis_uri(analysis) == EMI_RES["analysis/RUNX"]
    assert AnalysisURIs.analysis_metadata_uri(analysis) == EMI_RES["metadata/RUNX"]
    assert AnalysisURIs.featureset_uri(analysis) == EMI_RES["featureset/RUNX"]


def test_spectrum_and_adduct_uris(make_analysis, make_adduct):
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    assert AnalysisURIs.spectrum_uri(analysis, spectrum) == EMI_RES[
        f"spectrum/RUNX/{spectrum.feature_id}"
    ]

    adduct = make_adduct()
    # A ChemicalAdduct is a (formula group, recipe) pairing, so the key carries both
    # halves — the recipe hash *and* the group's formula ("C2H6" by fixture default).
    assert AnalysisURIs.chemical_adduct_uri(analysis, spectrum, adduct) == EMI_RES[
        f"adduct/RUNX/{spectrum.feature_id}/{_recipe_hash(adduct.recipe)}/C2H6"
    ]
    # The globally shared recipe node is unaffected — _recipe_hash did not change.
    assert AnalysisURIs.recipe_uri(adduct.recipe) == EMI_RES[
        f"recipe/{_recipe_hash(adduct.recipe)}"
    ]


def test_adduct_uri_separates_compounds_under_one_recipe(
    make_analysis, make_adduct, make_lotus, make_recipe
):
    """The regression this key exists for: two molecules proposed for the same feature
    under the same ionization form are two hypotheses, so two nodes.

    Keyed on the recipe alone they collapsed into one, which kept the first
    hypothesis' chemistry and collected a conflicting rank from the second.
    """
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    recipe = make_recipe(ingredients={"proton": 1})
    aspirin = make_adduct(
        lotus=[make_lotus(structure_molecular_formula="C9H8O4",
                          structure_exact_mass=180.04226)],
        recipe=recipe,
    )
    other = make_adduct(
        lotus=[make_lotus(structure_molecular_formula="C10H12O2",
                          structure_exact_mass=164.08373)],
        recipe=recipe,
    )
    assert AnalysisURIs.chemical_adduct_uri(
        analysis, spectrum, aspirin
    ) != AnalysisURIs.chemical_adduct_uri(analysis, spectrum, other)


def test_adduct_uri_still_separates_recipes_under_one_formula(
    make_analysis, make_adduct, make_lotus, make_recipe
):
    """The original discriminator still discriminates — adding the formula must not
    collapse the adduct *forms* of one molecule onto a single node."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    lotus = make_lotus()
    form_h = make_adduct(lotus=[lotus], recipe=make_recipe(ingredients={"proton": 1}))
    form_na = make_adduct(lotus=[lotus], recipe=make_recipe(ingredients={"sodium": 1}))
    assert AnalysisURIs.chemical_adduct_uri(
        analysis, spectrum, form_h
    ) != AnalysisURIs.chemical_adduct_uri(analysis, spectrum, form_na)


def test_adduct_uri_is_stable_across_group_member_order(
    make_analysis, make_adduct, make_lotus, make_recipe
):
    """One hypothesis = one node: the key names the formula *group*, so the order of
    the isobaric members inside it cannot move the IRI."""
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    recipe = make_recipe()
    # validate_lotus requires one shared formula across the group, so both use the default.
    first = make_lotus(structure_inchikey="AAAAAAAAAAAAAA-UHFFFAOYSA-N")
    second = make_lotus(structure_inchikey="BBBBBBBBBBBBBB-UHFFFAOYSA-N")
    assert AnalysisURIs.chemical_adduct_uri(
        analysis, spectrum, make_adduct(lotus=[first, second], recipe=recipe)
    ) == AnalysisURIs.chemical_adduct_uri(
        analysis, spectrum, make_adduct(lotus=[second, first], recipe=recipe)
    )


def test_formula_segment_percent_encodes(make_adduct, make_lotus):
    """Nothing validates a LOTUS formula, so a stray '/' must not reshape the path."""
    adduct = make_adduct(lotus=[make_lotus(structure_molecular_formula="C6H5O6- /x")])
    segment = _formula_segment(adduct)
    assert "/" not in segment and " " not in segment
    assert segment == "C6H5O6-%20%2Fx"


def test_formula_segment_falls_back_to_the_group_exact_mass(make_adduct, make_lotus):
    """A missing or blank formula must not collapse two hypotheses back onto one node,
    and must not raise — the fallback keys on the exact mass, which defines the group."""
    missing = make_adduct(lotus=[make_lotus(structure_molecular_formula=None,
                                            structure_exact_mass=180.04226)])
    blank = make_adduct(lotus=[make_lotus(structure_molecular_formula="   ",
                                          structure_exact_mass=164.08373)])
    assert _formula_segment(missing) == "mass-180.042260"
    assert _formula_segment(missing) != _formula_segment(blank)


def test_sirius_annotation_uri(make_analysis, make_sirius_annotation):
    analysis = make_analysis(run_name="RUNX", n_spectra=1)
    spectrum = analysis.spectra[0]
    annotation = make_sirius_annotation(inchikey_2d="BBTZETLXNQDZKF")
    # Keyed on run + feature + 2D InChIKey (structural identity); rank rides as a property.
    assert AnalysisURIs.sirius_annotation_uri(analysis, spectrum, annotation) == EMI_RES[
        f"sirius/RUNX/{spectrum.feature_id}/BBTZETLXNQDZKF"
    ]


def test_lotus_uri_cascade(make_lotus):
    with_inchikey = make_lotus(structure_inchikey="ABCDEFGHIJKLMN-OPQRSTUVWX-Y")
    assert CompoundURIs.lotus_uri(with_inchikey) == INCHIKEY["ABCDEFGHIJKLMN-OPQRSTUVWX-Y"]

    with_wikidata = make_lotus(
        structure_inchikey=None, structure_wikidata="http://www.wikidata.org/entity/Q42"
    )
    assert CompoundURIs.lotus_uri(with_wikidata) == URIRef("http://www.wikidata.org/entity/Q42")

    smiles_only = make_lotus(
        structure_inchikey=None, structure_wikidata=None, structure_smiles="CCO"
    )
    assert CompoundURIs.lotus_uri(smiles_only) == EMI_RES[f"compound/smiles/{_short_hash('CCO')}"]


def test_organism_uri_cascade():
    assert OrganismURIs.organism_uri(
        _organism(wikidata="http://www.wikidata.org/entity/Q1", ott_id=5)
    ) == URIRef("http://www.wikidata.org/entity/Q1")
    assert OrganismURIs.organism_uri(_organism(ott_id=5)) == EMI_RES["taxon/ott/5"]
    assert OrganismURIs.organism_uri(_organism()) is None
    # direct helper agrees with the class method
    assert _organism_uri(None, 5) == EMI_RES["taxon/ott/5"]
