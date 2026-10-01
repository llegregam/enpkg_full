"""Test suite for the MS2 Enhancer."""

import logging
from time import time
from typing import Any

import numpy as np
import pytest
from matchms import Spectrum

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.enhancers.ms2_enhancer import Ms2Enhancer
from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.loaders.spectral_library_store import (
    LibraryCandidate,
    SpectralLibraryStore,
)
from enpkg.tests.test_enhancers.conftest import FIXTURE_DATASET


@pytest.fixture(scope="class")
def analysis():
    """Load a test analysis."""
    return AnalysisLoader.from_files(
        path_to_spectra=FIXTURE_DATASET / "msdata/processed/arnica_0_125_pos_merged.mgf",
        path_to_metadata=FIXTURE_DATASET / "metadata/metadata.tsv",
        path_to_quant_table=FIXTURE_DATASET / "msdata/processed/arnica_0_125_pos_merged_quant.csv",
        ionization_mode="pos",
    )


@pytest.fixture(scope="class")
def library_store(
    ms_enhancer_config: MSEnhancerConfig, logger: logging.Logger
) -> SpectralLibraryStore:
    return SpectralLibraryStore(
        duckdb_path=ms_enhancer_config.duckdb_path,
        logger=logger,
        library_names=ms_enhancer_config.spectral_libraries,
    )


@pytest.fixture(scope="class")
def lotus_store(ms_enhancer_config: MSEnhancerConfig, logger: logging.Logger) -> LotusStore:
    return LotusStore(
        duckdb_path=ms_enhancer_config.duckdb_path,
        logger=logger,
    )


@pytest.fixture(scope="class")
def ms2_enhancer(
    ms_enhancer_config: MSEnhancerConfig,
    logger: logging.Logger,
    library_store: SpectralLibraryStore,
    lotus_store: LotusStore,
) -> Ms2Enhancer:
    return Ms2Enhancer(
        configuration=ms_enhancer_config, logger=logger,
        library_store=library_store, lotus_store=lotus_store,
    )


_SHORT_IK = "LIBRARYONLYIKX"
_FULL_IK = f"{_SHORT_IK}-UHFFFAOYSA-N"


def _candidate(**overrides) -> LibraryCandidate:
    """A library candidate carrying every structure field FragHub can supply."""
    fields = dict(
        spectrum=Spectrum(
            mz=np.array([100.0, 150.0]),
            intensities=np.array([0.5, 1.0]),
            metadata={"precursor_mz": 200.0},
        ),
        library_id=0,
        inchikey=_FULL_IK,
        short_inchikey=_SHORT_IK,
        inchi="InChI=1S/C2H6/c1-2/h1-2H3",
        smiles="CC",
        molecular_formula="C2H6",
        compound_name="Library-only compound",
        adduct="[M+H]+",
        splash="splash10-0000",
        npc_pathway="Alkaloids|Terpenoids",
        npc_superclass="Tryptophan alkaloids",
        npc_class="Corynanthe type",
        classyfire_superclass="Organoheterocyclic compounds",
        classyfire_class="Indoles",
        classyfire_subclass="Indolines",
    )
    fields.update(overrides)
    return LibraryCandidate(**fields)


class TestAnnotate:
    """Which annotation `_annotate` builds for each kind of library candidate."""

    def test_lotus_backed_match_carries_lotus_and_organisms(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, make_lotus, monkeypatch
    ) -> None:
        lotus = make_lotus(structure_inchikey=_FULL_IK)
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {_SHORT_IK: [lotus]})
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(spectrum, _candidate(), "LIB:1.0", 0.9, 7)

        assert outcome == "lotus"
        annotation = spectrum.ms2_annotations[0]
        assert annotation.source == "Lotus"
        assert annotation.lotus is lotus
        assert annotation.library_structure is None
        assert annotation.has_organisms()

    def test_library_only_match_carries_the_library_structure(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch
    ) -> None:
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(spectrum, _candidate(), "LIB:1.0", 0.9, 7)

        assert outcome == "library"
        annotation = spectrum.ms2_annotations[0]
        assert annotation.source == "LIB:1.0"
        assert annotation.lotus is None
        assert annotation.short_inchikey == _SHORT_IK
        structure = annotation.library_structure
        assert structure is not None
        assert structure.inchikey == _FULL_IK
        assert structure.inchi == "InChI=1S/C2H6/c1-2/h1-2H3"
        assert structure.smiles == "CC"
        assert structure.compound_name == "Library-only compound"
        assert structure.npc_pathway == "Alkaloids|Terpenoids"

    def test_library_only_match_stays_out_of_the_reweighting(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch
    ) -> None:
        """No organism keeps it out of the feature matrices; the zero vectors are
        still full length so every elementwise product downstream lines up."""
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        spectrum = make_spectrum()

        ms2_enhancer._annotate(spectrum, _candidate(), "LIB:1.0", 0.9, 7)

        annotation = spectrum.ms2_annotations[0]
        assert not annotation.has_organisms()
        store = ms2_enhancer.lotus_store
        assert len(annotation.pathway_scores) == store.number_of_pathways
        assert len(annotation.superclass_scores) == store.number_of_superclasses
        assert len(annotation.class_scores) == store.number_of_classes
        assert not annotation.pathway_scores.any()

    @pytest.mark.parametrize(
        "inchikey",
        [
            "CCCCCCCCCCCCCC",               # a SMILES in the InChIKey field (FragHub export)
            "NCCCNCCCCNCCCN",               # likewise: 14 uppercase letters, no blocks
            "libraryonlyikx-uhfffaoysa-n",  # right shape, wrong case
            f"{_FULL_IK} ",                 # trailing whitespace
        ],
    )
    def test_malformed_inchikey_is_not_annotated(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch, inchikey
    ) -> None:
        """The full key would become the structure's identity and the 2D key is
        derived from it, so a malformed one leaves nothing trustworthy to file under."""
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(
            spectrum,
            _candidate(inchikey=inchikey, short_inchikey=inchikey[:14].upper()),
            "LIB:1.0", 0.9, 7,
        )

        assert outcome == "malformed_inchikey"
        assert spectrum.ms2_annotations == []

    def test_lotus_backed_match_is_not_subject_to_the_inchikey_check(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, make_lotus, monkeypatch
    ) -> None:
        """LOTUS supplies the structure identity itself, so the library's own
        InChIKey field is never read on that path."""
        lotus = make_lotus(structure_inchikey=_FULL_IK)
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {_SHORT_IK: [lotus]})
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(
            spectrum, _candidate(inchikey="not an inchikey"), "LIB:1.0", 0.9, 7
        )

        assert outcome == "lotus"

    def test_candidate_without_a_full_inchikey_is_not_annotated(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch
    ) -> None:
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(
            spectrum, _candidate(inchikey=None), "LIB:1.0", 0.9, 7
        )

        assert outcome == "no_hit"
        assert spectrum.ms2_annotations == []

    def test_candidate_naming_no_structure_is_not_annotated(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch
    ) -> None:
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(
            spectrum, _candidate(short_inchikey=None), "LIB:1.0", 0.9, 7
        )

        assert outcome == "no_hit"
        assert spectrum.ms2_annotations == []


class TestMalformedInchikeyReport:
    """A run that meets malformed InChIKeys says so, naming the offending values."""

    def test_enhance_warns_with_the_malformed_values(
        self, ms2_enhancer: Ms2Enhancer, make_analysis, make_spectrum, monkeypatch, caplog
    ) -> None:
        # Identical peak lists, so the pair clears min_score and min_peaks (8 >= 6).
        peaks = np.array([100.0, 120.0, 140.0, 160.0, 180.0, 200.0, 220.0, 240.0])
        intensities = np.linspace(0.3, 1.0, peaks.size)
        query = make_spectrum(mz=peaks, intensities=intensities, precursor_mz=300.0)
        analysis = make_analysis(n_spectra=1).model_copy(update={"spectra": (query,)})
        library_id = ms2_enhancer.library_store.libraries_for_mode("pos")[0].library_id
        smiles_as_inchikey = _candidate(
            spectrum=Spectrum(mz=peaks, intensities=intensities, metadata={"precursor_mz": 300.0}),
            library_id=library_id,
            inchikey="CCCCCCCCCCCCCC",
            short_inchikey="CCCCCCCCCCCCCC",
        )
        monkeypatch.setattr(
            ms2_enhancer.library_store,
            "candidates_for",
            lambda precursor_mzs, mode, tolerance: ([smiles_as_inchikey], [(0, 0)]),
        )

        with caplog.at_level(logging.WARNING):
            ms2_enhancer.enhance(analysis)

        assert query.ms2_annotations == []
        assert "malformed InChIKey" in caplog.text
        assert "'CCCCCCCCCCCCCC'" in caplog.text


class TestMs2Enhancer:
    """Test class for Ms2Enhancer."""

    def test_initialization(self, ms2_enhancer: Ms2Enhancer, logger: logging.Logger) -> None:
        """Test that Ms2Enhancer correctly initializes and defers Lotus loading to first enhance().

        Args:
            ms2_enhancer: The MS2 enhancer instance correctly initialized via fixtures.
            logger: A logger instance.
        """
        assert ms2_enhancer is not None, "Ms2Enhancer should not be None."
        # Lotus objects are now built lazily on first enhance() call, not at __init__.
        assert ms2_enhancer.lotus_objects is None, "Ms2Enhancer lotus_objects should be None before enhance()."
        assert ms2_enhancer.name() == "MS2 Enhancer", "Ms2Enhancer name property should match."

    def test_enhance_spectra(self, ms2_enhancer: Ms2Enhancer, analysis: Any, logger: logging.Logger) -> None:
        """Test the MS2 spectrum enrichment.

        Args:
            ms2_enhancer: The fully populated Ms2Enhancer instance.
            analysis: Main analysis dataset loaded from project test directory.
            logger: A contextual logger.
        """
        assert analysis.spectra is not None, "Analysis should have spectra to pass to MS2 enhancer."
        original_spectra_count = len(analysis.spectra)

        start = time()
        enriched = ms2_enhancer.enhance(analysis, chunk_size=1000)
        logger.info(f"Enhanced {len(enriched.spectra)} MS2 spectra in {time() - start:.2f} seconds")

        assert enriched is not None, "A valid Analysis must be returned."
        assert len(enriched.spectra) == original_spectra_count, "The same number of spectra must be returned."

