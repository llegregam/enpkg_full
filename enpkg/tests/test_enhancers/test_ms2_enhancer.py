"""Test suite for the MS2 Enhancer."""

import logging
from time import time
from typing import Any

import numpy as np
import pytest
from matchms import Spectrum

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.chemical_annotation import MS2ChemicalAnnotation
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

    def test_weak_library_only_match_is_not_annotated(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch
    ) -> None:
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        params = ms2_enhancer.configuration.spectral_match_params
        monkeypatch.setattr(params, "library_only_min_score", 0.7)
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(spectrum, _candidate(), "LIB:1.0", 0.69, 7)

        assert outcome == "weak_library_only"
        assert spectrum.ms2_annotations == []

    @pytest.mark.parametrize(("floor", "annotated"), [(0.7, True), (0.71, False), (0.2, True)])
    def test_library_only_floor_is_inclusive_and_configurable(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch, floor, annotated
    ) -> None:
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        params = ms2_enhancer.configuration.spectral_match_params
        monkeypatch.setattr(params, "library_only_min_score", floor)
        spectrum = make_spectrum()

        ms2_enhancer._annotate(spectrum, _candidate(), "LIB:1.0", 0.7, 7)

        assert bool(spectrum.ms2_annotations) is annotated

    def test_lotus_backed_match_is_not_subject_to_the_library_only_floor(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, make_lotus, monkeypatch
    ) -> None:
        lotus = make_lotus(structure_inchikey=_FULL_IK)
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {_SHORT_IK: [lotus]})
        params = ms2_enhancer.configuration.spectral_match_params
        monkeypatch.setattr(params, "library_only_min_score", 0.7)
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(spectrum, _candidate(), "LIB:1.0", 0.3, 7)

        assert outcome == "lotus"

    def test_malformed_inchikey_is_reported_whatever_the_score(
        self, ms2_enhancer: Ms2Enhancer, make_spectrum, monkeypatch
    ) -> None:
        """The InChIKey is checked before the floor, so the report stays complete."""
        monkeypatch.setattr(ms2_enhancer, "_lotus_by_short_inchikey", {})
        params = ms2_enhancer.configuration.spectral_match_params
        monkeypatch.setattr(params, "library_only_min_score", 0.7)
        spectrum = make_spectrum()

        outcome = ms2_enhancer._annotate(
            spectrum,
            _candidate(inchikey="CCCCCCCCCCCCCC", short_inchikey="CCCCCCCCCCCCCC"),
            "LIB:1.0", 0.3, 7,
        )

        assert outcome == "malformed_inchikey"

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


_PEAKS = np.array([100.0, 120.0, 140.0, 160.0, 180.0, 200.0, 220.0, 240.0])
_INTENSITIES = np.linspace(0.3, 1.0, _PEAKS.size)


def _library_candidate(ms2_enhancer: Ms2Enhancer, intensities, **overrides) -> LibraryCandidate:
    """A candidate on the query's eight peaks; ``intensities`` sets its cosine."""
    library_id = ms2_enhancer.library_store.libraries_for_mode("pos")[0].library_id
    spectrum = Spectrum(mz=_PEAKS, intensities=intensities, metadata={"precursor_mz": 300.0})
    return _candidate(spectrum=spectrum, library_id=library_id, **overrides)


def _enhance_one_feature(ms2_enhancer, make_analysis, make_spectrum, monkeypatch, candidates):
    """Run ``enhance`` on one feature whose candidates are ``candidates``, in that order."""
    query = make_spectrum(mz=_PEAKS, intensities=_INTENSITIES, precursor_mz=300.0)
    analysis = make_analysis(n_spectra=1).model_copy(update={"spectra": (query,)})
    monkeypatch.setattr(
        ms2_enhancer.library_store,
        "candidates_for",
        lambda precursor_mzs, mode, tolerance: (
            candidates, [(0, index) for index in range(len(candidates))]
        ),
    )
    ms2_enhancer.enhance(analysis)
    return query


def _annotation(score: float, n_matched_peaks: int, library: str = "LIB:1.0"):
    zeros = np.zeros(2, dtype=np.float32)
    return MS2ChemicalAnnotation(
        source=library, queried_against=library, short_inchikey=_SHORT_IK,
        score=score, n_matched_peaks=n_matched_peaks, algorithm="cosine_greedy",
        pathway_scores=zeros, superclass_scores=zeros, class_scores=zeros, organisms=[],
    )


class TestOneAnnotationPerStructure:
    """A structure matched on one feature through several library spectra is annotated once."""

    def test_the_best_scoring_spectrum_is_kept(
        self, ms2_enhancer: Ms2Enhancer, make_analysis, make_spectrum, monkeypatch
    ) -> None:
        weaker = _library_candidate(ms2_enhancer, _INTENSITIES[::-1], compound_name="weaker")
        stronger = _library_candidate(ms2_enhancer, _INTENSITIES, compound_name="stronger")

        query = _enhance_one_feature(
            ms2_enhancer, make_analysis, make_spectrum, monkeypatch, [weaker, stronger]
        )

        assert len(query.ms2_annotations) == 1
        annotation = query.ms2_annotations[0]
        assert annotation.score == pytest.approx(1.0)
        assert annotation.library_structure.compound_name == "stronger"

    def test_on_a_full_tie_the_first_listed_spectrum_is_kept(
        self, ms2_enhancer: Ms2Enhancer, make_analysis, make_spectrum, monkeypatch
    ) -> None:
        """Candidates arrive ordered by library spectrum id, so the lowest id wins."""
        first = _library_candidate(ms2_enhancer, _INTENSITIES, compound_name="first")
        second = _library_candidate(ms2_enhancer, _INTENSITIES, compound_name="second")

        query = _enhance_one_feature(
            ms2_enhancer, make_analysis, make_spectrum, monkeypatch, [first, second]
        )

        assert [a.library_structure.compound_name for a in query.ms2_annotations] == ["first"]

    def test_different_structures_stay_apart(
        self, ms2_enhancer: Ms2Enhancer, make_analysis, make_spectrum, monkeypatch
    ) -> None:
        one = _library_candidate(ms2_enhancer, _INTENSITIES)
        other = _library_candidate(
            ms2_enhancer, _INTENSITIES,
            inchikey="OTHERSTRUCTURE-UHFFFAOYSA-N", short_inchikey="OTHERSTRUCTURE",
        )

        query = _enhance_one_feature(
            ms2_enhancer, make_analysis, make_spectrum, monkeypatch, [one, other]
        )

        assert [a.short_inchikey for a in query.ms2_annotations] == [_SHORT_IK, "OTHERSTRUCTURE"]

    def test_merged_matches_are_reported(
        self, ms2_enhancer: Ms2Enhancer, make_analysis, make_spectrum, monkeypatch, caplog
    ) -> None:
        candidates = [
            _library_candidate(ms2_enhancer, _INTENSITIES),
            _library_candidate(ms2_enhancer, _INTENSITIES[::-1]),
        ]

        with caplog.at_level(logging.INFO):
            _enhance_one_feature(
                ms2_enhancer, make_analysis, make_spectrum, monkeypatch, candidates
            )

        assert "added 1 annotations (0 LOTUS-backed, 1 library-only)" in caplog.text
        assert "1 further scored matches named a structure already matched" in caplog.text

    def test_more_matched_peaks_break_a_score_tie(self, make_spectrum) -> None:
        spectrum = make_spectrum()
        spectrum.ms2_annotations = [_annotation(0.8, 6), _annotation(0.8, 9), _annotation(0.7, 12)]

        dropped = Ms2Enhancer._keep_best_match_per_structure(spectrum)

        assert [a.n_matched_peaks for a in spectrum.ms2_annotations] == [9]
        assert sorted(a.n_matched_peaks for a in dropped) == [6, 12]

    def test_each_library_keeps_its_own_match(self, make_spectrum) -> None:
        """The graph keys an MS2 annotation on library and structure, so both stay."""
        spectrum = make_spectrum()
        spectrum.ms2_annotations = [_annotation(0.8, 6, "A:1"), _annotation(0.9, 6, "B:1")]

        assert Ms2Enhancer._keep_best_match_per_structure(spectrum) == []
        assert len(spectrum.ms2_annotations) == 2


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

