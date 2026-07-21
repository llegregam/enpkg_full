"""Parser for SIRIUS command-line output summaries.

SIRIUS writes, per subject, a set of tab-separated summary files. This module
maps those files onto the :class:`SiriusResults` dataclass so the rest of the
pipeline can consume them as ``pandas`` frames. It is independent of the RDF
layer (it only reads SIRIUS output); the RDF serializer consumes the parsed
results downstream once SIRIUS ingestion is wired onto the data model.
"""

import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import List, Optional

import pandas as pd

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.sirius_annotation import SiriusChemicalAnnotation

# SIRIUS top-k summaries end in "_top-N" (most) or "-N" (canopus_formula_summary);
# N is the --top-k-summary value. Presence of the suffix marks the *_top variant.
_TOP_K_SUFFIX = re.compile(r"(?:_top)?-\d+$")


@dataclass
class SiriusResults:
    """Parsed SIRIUS summary frames, one field per known summary file."""

    canopus_formula_summary: pd.DataFrame | None = None
    canopus_formula_summary_top: pd.DataFrame | None = None
    canopus_structure_summary: pd.DataFrame | None = None
    canopus_structure_summary_top: pd.DataFrame | None = None
    denovo_structure_identifications: pd.DataFrame | None = None
    denovo_structure_identifications_top: pd.DataFrame | None = None
    formula_identifications: pd.DataFrame | None = None
    formula_identifications_top: pd.DataFrame | None = None
    spectral_matches_analog: pd.DataFrame | None = None
    spectral_matches_analog_top: pd.DataFrame | None = None
    spectral_matches: pd.DataFrame | None = None
    spectral_matches_top: pd.DataFrame | None = None
    structure_identifications: pd.DataFrame | None = None
    structure_identifications_top: pd.DataFrame | None = None


class SiriusOutputParser:
    """Parser for the output of the Sirius enhancer."""

    def __init__(self, paths: List[str]):

        if not isinstance(paths, list):
            raise ValueError(f"Paths must be a list of strings. Detected type: {type(paths)}")
        if len(paths) == 0:
            raise ValueError("Paths list cannot be empty.")
        if not all(isinstance(path, str) for path in paths):
            raise ValueError(f"All paths must be strings. Detected types: {', '.join(set(type(path) for path in paths))}")
        self.paths = paths
        self.results: SiriusResults | None = None

    @classmethod
    def digest_paths(cls, paths: List[str]) -> "SiriusOutputParser":
        """
        Create a SiriusOutputParser from a list of Sirius output file paths.

        Each path is mapped to a field of :class:`SiriusResults` by its filename
        stem. SIRIUS writes, per subject, a plain summary (one candidate per
        feature) and usually a top-k summary (up to k candidates per feature, the
        ``--top-k-summary`` output). The top-k file carries a trailing ``_top-N``
        suffix (``-N`` for ``canopus_formula_summary``); its presence routes the
        file to the matching ``*_top`` field. Files that don't map to a known
        field are skipped.

        Args:
            paths (List[str]): List of paths of the different Sirius output files.
        Returns:
            SiriusOutputParser: parser whose ``.results`` holds the parsed frames
            in a populated :class:`SiriusResults` dataclass.
        """

        parser = cls(paths)  # reuse __init__ path validation
        results = SiriusResults()
        valid_fields = {f.name for f in fields(results)}
        for path in paths:
            stem = Path(path).stem                # filename
            base = _TOP_K_SUFFIX.sub("", stem)    # strip trailing _top-N / -N
            field = base if base == stem else f"{base}_top"
            if field not in valid_fields:
                continue                          # unrecognized file -> skip
            setattr(results, field, pd.read_csv(path, sep="\t"))
        parser.results = results
        return parser


# Columns of `structure_identifications_top-X.tsv` consumed when attaching SIRIUS
# annotations onto the data model. Selecting only these before `itertuples()` keeps
# a colon-bearing column name (e.g. "CSI:FingerIDScore") from disrupting attribute
# access. Note the lowercase 'k' in `InChIkey2D` — that is SIRIUS's header spelling.
_STRUCTURE_ID_COLUMNS = [
    "mappingFeatureId",     # join key -> AnnotatedSpectrum.feature_id
    "structurePerIdRank",   # candidate rank for its feature (1 = best)
    "molecularFormula",
    "adduct",
    "InChIkey2D",
]

_ADDUCT_WHITESPACE = re.compile(r"\s+")


def _clean_str(value: object) -> str:
    """Return a stripped string, or ``""`` for None/NaN (so the serializer skips it)."""
    if value is None or (isinstance(value, float) and value != value):  # None/NaN
        return ""
    return str(value).strip()


def _normalize_adduct(value: object) -> str:
    """Compact SIRIUS adduct form: ``"[M + K]+"`` -> ``"[M+K]+"``; ``""`` for missing.

    Matches the spaceless bracket form the serializer emits for MS1 adducts.
    """
    return _ADDUCT_WHITESPACE.sub("", _clean_str(value))


def _as_int(value: object) -> Optional[int]:
    """Coerce a pandas cell to ``int``, or ``None`` for None/NaN/unparseable."""
    if value is None or (isinstance(value, float) and value != value):  # None/NaN
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def attach_sirius_annotations(
    analysis: Analysis, results: SiriusResults, *, top_k: Optional[int] = None
) -> Analysis:
    """Attach SIRIUS structure identifications onto the analysis's spectra.

    Reads ``results.structure_identifications_top`` (the top-k structure summary),
    builds one :class:`SiriusChemicalAnnotation` per row, and attaches it to the
    matching spectrum by joining the SIRIUS ``mappingFeatureId`` column onto each
    spectrum's ``feature_id`` (both originate from the MGF ``FEATURE_ID``). Spectra
    with no SIRIUS rows are left untouched; rows whose feature is absent from the
    analysis are ignored. Rows missing a feature id, rank, or 2D InChIKey (no usable
    identity) are skipped. Mutates the spectra in place — as the MS1/MS2 enhancers do
    — and returns the same ``analysis``.

    Args:
        analysis: The analysis whose spectra receive the annotations.
        results: Parsed SIRIUS summaries (from :meth:`SiriusOutputParser.digest_paths`).
        top_k: Optional cap on candidates per feature; rows with
            ``structurePerIdRank > top_k`` are dropped. ``None`` keeps every row.

    Returns:
        The same ``analysis`` instance, with ``spectrum.sirius_annotations`` populated.
    """
    frame = results.structure_identifications_top
    if frame is None or frame.empty:
        return analysis

    missing = [column for column in _STRUCTURE_ID_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(
            f"SIRIUS structure-identifications summary is missing columns {missing}; "
            f"present columns: {list(frame.columns)}"
        )

    by_feature: dict[int, list[SiriusChemicalAnnotation]] = {}
    for row in frame[_STRUCTURE_ID_COLUMNS].itertuples(index=False):
        feature_id = _as_int(row.mappingFeatureId)
        rank = _as_int(row.structurePerIdRank)
        inchikey_2d = _clean_str(row.InChIkey2D)
        if feature_id is None or rank is None or not inchikey_2d:
            continue  # no feature / rank / structure identity -> not a usable annotation
        if top_k is not None and rank > top_k:
            continue
        by_feature.setdefault(feature_id, []).append(
            SiriusChemicalAnnotation(
                rank=rank,
                molecular_formula=_clean_str(row.molecularFormula),
                adduct=_normalize_adduct(row.adduct),
                inchikey_2d=inchikey_2d,
            )
        )

    for spectrum in analysis.spectra:
        annotations = by_feature.get(spectrum.feature_id)
        if annotations:
            spectrum.sirius_annotations = sorted(annotations, key=lambda annotation: annotation.rank)
    return analysis
