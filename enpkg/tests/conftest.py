"""Shared object factories for the pure data-model unit tests.

These build small, valid instances in memory (no DuckDB, no network, no files)
so the tests are fast and deterministic.
"""

import numpy as np
import pytest
from matchms import Spectrum

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.annotated_spectra_class import AnnotatedSpectrum
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.ms1_data_classes.adduct_class import AdductRecipe, ChemicalAdduct
from enpkg.monolith.data.sample_metadata import SampleMetadata
from enpkg.monolith.data.sirius_annotation import SiriusChemicalAnnotation


def pytest_collection_modifyitems(config, items):
    """Auto-mark the DB/network-backed enhancer suite as ``integration``.

    Everything under ``tests/test_enhancers`` needs the DuckDB database, network
    access (Open Tree of Life), or an external tool, so it is marked
    ``integration`` here rather than in each file. The fast default suite (the
    data-model and pipeline unit tests) then runs via ``pytest -m "not
    integration"`` — which is what CI uses.
    """
    for item in items:
        if "test_enhancers" in str(item.fspath):
            item.add_marker(pytest.mark.integration)

_LOTUS_DEFAULTS = dict(
    structure_wikidata="Q123",
    structure_inchikey="VNJWNFJMXRGDHO-UHFFFAOYSA-N",
    structure_inchi="InChI=1S/C2H6/c1-2/h1-2H3",
    structure_smiles="CC",
    structure_molecular_formula="C2H6",
    structure_exact_mass=30.04,
    structure_xlogp=1.2,
    structure_smiles_2d="CC",
    structure_cid=12345,
    structure_name_iupac="ethane",
    structure_name_traditional="ethane",
    structure_stereocenters_total=0,
    structure_stereocenters_unspecified=0,
    structure_taxonomy_hammer_pathways=np.array([1.0, 0.0]),
    structure_taxonomy_hammer_superclasses=np.array([1.0, 0.0]),
    structure_taxonomy_hammer_classes=np.array([1.0, 0.0]),
    structure_taxonomy_classyfire_chemontid="CHEMONTID:1",
    structure_taxonomy_classyfire_01kingdom="Organic",
    structure_taxonomy_classyfire_02superclass="Super",
    structure_taxonomy_classyfire_03class="Class",
    structure_taxonomy_classyfire_04directparent="Parent",
    organism_wikidata="Q456",
    organism_name="Test organism",
    organism_taxonomy_gbifid=111,
    organism_taxonomy_ncbiid=222,
    organism_taxonomy_ottid=333,
    domain="Eukaryota",
    kingdom="Plantae",
    phylum="Tracheophyta",
    klass="Magnoliopsida",
    order="Asterales",
    family="Asteraceae",
    tribe="Anthemideae",
    genus="Artemisia",
    species="Artemisia annua",
    varietas=None,
    reference_wikidata="Q789",
    reference_doi="10.1/xyz",
    manual_validation=True,
)


@pytest.fixture
def make_lotus():
    """Return a factory building a fully-populated Lotus, overridable per field."""

    def _make(**overrides) -> Lotus:
        return Lotus(**{**_LOTUS_DEFAULTS, **overrides})

    return _make


@pytest.fixture
def make_recipe():
    """Return a factory building an AdductRecipe (defaults to protonation [M+H]+)."""

    def _make(ingredients=None, charge=1, positive=True, multimer_factor=1.0) -> AdductRecipe:
        return AdductRecipe(
            ingredients=ingredients if ingredients is not None else {"proton": 1},
            charge=charge,
            positive=positive,
            multimer_factor=multimer_factor,
        )

    return _make


@pytest.fixture
def make_adduct(make_lotus, make_recipe):
    """Return a factory building a ChemicalAdduct over one Lotus group."""

    def _make(lotus=None, recipe=None) -> ChemicalAdduct:
        return ChemicalAdduct(
            lotus=lotus if lotus is not None else [make_lotus()],
            recipe=recipe if recipe is not None else make_recipe(),
        )

    return _make


@pytest.fixture
def make_spectrum():
    """Return a factory building an AnnotatedSpectrum from synthetic peaks."""

    def _make(
        feature_id: int = 1,
        precursor_mz: float = 200.0,
        charge: int = 1,
        mz=None,
        intensities=None,
        retention_time: float = 1.0,
        intensity: float = 1000.0,
        sirius_annotations=None,
    ) -> AnnotatedSpectrum:
        mz = np.array([100.0, 150.0]) if mz is None else np.asarray(mz, dtype=float)
        intensities = (
            np.array([0.5, 1.0]) if intensities is None else np.asarray(intensities, dtype=float)
        )
        base = Spectrum(
            mz=mz,
            intensities=intensities,
            metadata={
                "precursor_mz": precursor_mz,
                "feature_id": feature_id,
                "charge": charge,
            },
        )
        spectrum = AnnotatedSpectrum(
            base,
            mass_over_charge=precursor_mz,
            retention_time=retention_time,
            intensity=intensity,
        )
        if sirius_annotations is not None:
            spectrum.sirius_annotations = list(sirius_annotations)
        return spectrum

    return _make


@pytest.fixture
def make_sirius_annotation():
    """Return a factory building a SiriusChemicalAnnotation (defaults to a rank-1 hit)."""

    def _make(
        rank: int = 1,
        molecular_formula: str = "C6H9N3O3S",
        adduct: str = "[M+K]+",
        inchikey_2d: str = "BBTZETLXNQDZKF",
    ) -> SiriusChemicalAnnotation:
        return SiriusChemicalAnnotation(
            rank=rank,
            molecular_formula=molecular_formula,
            adduct=adduct,
            inchikey_2d=inchikey_2d,
        )

    return _make


@pytest.fixture
def make_analysis(make_spectrum):
    """Return a factory building an Analysis with N synthetic spectra."""

    def _make(
        run_name: str = "RUN1",
        n_spectra: int = 1,
        source_taxon="Artemisia annua",
        sample_type=None,
        ionization_mode: str = "pos",
    ) -> Analysis:
        spectra = tuple(make_spectrum(feature_id=i + 1) for i in range(n_spectra))
        metadata = SampleMetadata(
            sample_id="S1", source_taxon=source_taxon, sample_type=sample_type
        )
        return Analysis(
            run_name=run_name,
            spectra=spectra,
            metadata=metadata,
            ionization_mode=ionization_mode,
        )

    return _make
