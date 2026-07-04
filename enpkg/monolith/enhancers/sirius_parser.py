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
from typing import List

import pandas as pd

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
