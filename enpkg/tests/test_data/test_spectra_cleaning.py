"""Unit tests for the F-01 spectra-cleaning port in AnalysisLoader."""

import numpy as np
from matchms import Spectrum

from enpkg.monolith.loaders.analysis_loader import AnalysisLoader

_CLEAN_KW = dict(mz_from=10.0, mz_to=1000.0, min_relative_intensity=0.01, min_peaks=1)


def _spec(mz, intensities, precursor_mz=200.0, feature_id=1):
    return Spectrum(
        mz=np.asarray(mz, dtype=float),
        intensities=np.asarray(intensities, dtype=float),
        metadata={"precursor_mz": precursor_mz, "feature_id": feature_id},
    )


def test_clean_windows_mz_and_normalizes():
    spectrum = _spec([5.0, 100.0, 150.0, 1200.0], [10.0, 100.0, 50.0, 100.0])
    cleaned = AnalysisLoader._clean_spectrum(spectrum, **_CLEAN_KW)
    assert cleaned is not None
    # peaks below mz_from (5.0) and above mz_to (1200.0) are removed
    assert list(cleaned.peaks.mz) == [100.0, 150.0]
    # normalised to a base peak of 1.0
    assert cleaned.peaks.intensities.max() == 1.0


def test_clean_drops_low_relative_intensity():
    # 4/1000 = 0.004 < min_relative_intensity 0.01 -> the second peak is dropped
    spectrum = _spec([100.0, 150.0], [1000.0, 4.0])
    cleaned = AnalysisLoader._clean_spectrum(spectrum, **_CLEAN_KW)
    assert list(cleaned.peaks.mz) == [100.0]


def test_clean_returns_none_below_min_peaks():
    spectrum = _spec([100.0], [1.0])
    cleaned = AnalysisLoader._clean_spectrum(
        spectrum, mz_from=10.0, mz_to=1000.0, min_relative_intensity=0.01, min_peaks=2
    )
    assert cleaned is None


def test_clean_preserves_precursor_and_feature_metadata():
    spectrum = _spec([100.0, 150.0], [1.0, 0.5], precursor_mz=250.0, feature_id=7)
    cleaned = AnalysisLoader._clean_spectrum(spectrum, **_CLEAN_KW)
    assert cleaned.get("precursor_mz") == 250.0
    assert cleaned.get("feature_id") == 7


def test_clean_spectra_drops_emptied_and_reports_count():
    good = _spec([100.0, 150.0], [1.0, 0.5])
    emptied = _spec([5.0], [1.0])  # only peak is outside the m/z window -> removed
    result = AnalysisLoader._clean_spectra((good, emptied), **_CLEAN_KW)
    assert len(result) == 1
    assert list(result[0].peaks.mz) == [100.0, 150.0]
