"""Unit tests for the deterministic RDF URI minting."""

from rdflib import URIRef

from enpkg.monolith.data.chemical_annotation import AnnotationOrganism
from enpkg.monolith.data.ms1_data_classes.adduct_class import AdductRecipe
from enpkg.monolith.rdf.namespaces import EMI_RES, INCHIKEY
from enpkg.monolith.rdf.uris import (
    AnalysisURIs,
    CompoundURIs,
    OrganismURIs,
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
    adduct_uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, adduct)
    assert str(adduct_uri).startswith(str(EMI_RES[f"adduct/RUNX/{spectrum.feature_id}/"]))
    # the discriminator is the recipe hash, not str(adduct)
    assert str(adduct_uri).endswith(_recipe_hash(adduct.recipe))
    assert AnalysisURIs.recipe_uri(adduct.recipe) == EMI_RES[
        f"recipe/{_recipe_hash(adduct.recipe)}"
    ]


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
