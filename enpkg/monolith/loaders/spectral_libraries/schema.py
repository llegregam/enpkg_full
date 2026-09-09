"""The FragHub CSV column contract and its mapping onto ``library_spectra``.

Spectral libraries reach this pipeline only after being processed by FragHub
(https://github.com/eMetaboHUB/FragHub), which harmonises any of MSP, MGF, JSON,
CSV and XML from any open library into one schema. Format diversity is therefore
handled upstream, and this module describes the single shape that arrives here.

The contract is *core required, extras allowed*: an import fails if a load-bearing
column is missing, while unrecognised columns are preserved in ``metadata_json``
and reported. That is what lets a FragHub release add fields without breaking
ingestion, while still making the change visible.
"""

from typing import Final

# Columns without which a spectrum cannot be stored or matched. PRECURSORMZ and
# PEAKS_LIST are the spectrum itself; MSLEVEL and IONMODE decide whether and how it
# is stored; INCHIKEY is the join key to LOTUS; PRECURSORTYPE is the ionisation form.
REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset({
    "PRECURSORMZ",
    "PEAKS_LIST",
    "MSLEVEL",
    "IONMODE",
    "INCHIKEY",
    "PRECURSORTYPE",
})

# FragHub source column -> library_spectra column, for values copied across as-is
# after missing-value normalisation. precursor_mz, mode, ms_level, mzs, intensities
# and short_inchikey are derived rather than copied, so they are not listed here.
COLUMN_MAP: Final[dict[str, str]] = {
    "INCHIKEY": "inchikey",
    "SMILES": "smiles",
    "FORMULA": "molecular_formula",
    "NAME": "compound_name",
    "PRECURSORTYPE": "adduct",
    "SPLASH": "splash",
    "NPCLASS_PATHWAY": "npc_pathway",
    "NPCLASS_SUPERCLASS": "npc_superclass",
    "NPCLASS_CLASS": "npc_class",
    "CLASSYFIRE_SUPERCLASS": "classyfire_superclass",
    "CLASSYFIRE_CLASS": "classyfire_class",
    "CLASSYFIRE_SUBCLASS": "classyfire_subclass",
}

# Carried into metadata_json rather than given a column: provenance and acquisition
# detail that nothing in the annotation path reads. Storing them keeps the import
# lossless without widening the table the candidate query scans.
METADATA_COLUMNS: Final[tuple[str, ...]] = (
    "FILENAME",
    "FILEHASH",
    "PREDICTED",
    "SPECTRUMID",
    "RESOLUTION",
    "SYNON",
    "IONIZATION",
    "FRAGMENTATIONMODE",
    "INSTRUMENTTYPE",
    "INSTRUMENT",
    "COLLISIONENERGY",
    "AVERAGEMASS",
    "RT",
    "COMMENT",
    "ENTROPY",
    "NUM PEAKS",
)

# Columns consumed by the import but neither copied nor stored as metadata: their
# values are transformed into derived columns instead.
_DERIVED_SOURCES: Final[tuple[str, ...]] = (
    "PRECURSORMZ",
    "PEAKS_LIST",
    "MSLEVEL",
    "IONMODE",
    "EXACTMASS",
)

# Every column the FragHub export is known to carry. A column outside this set is
# not an error; it is preserved and reported (see FragHubCsvImporter).
KNOWN_COLUMNS: Final[frozenset[str]] = frozenset(
    set(COLUMN_MAP) | set(METADATA_COLUMNS) | set(_DERIVED_SOURCES)
)

# Placeholders FragHub writes for an absent value. 'NOT FOUND' is what the observed
# exports use; the FragHub paper also documents 'UNKNOWN' for values such as
# `RT: 0.0` or `adduct: unknown`. Both are normalised to SQL NULL so that "absent"
# is representable once rather than as several magic strings.
MISSING_TOKENS: Final[tuple[str, ...]] = (
    "",
    "NOT FOUND",
    "UNKNOWN",
    "N/A",
    "NA",
    "NULL",
    "NONE",
    "-",
)


def quote_identifier(column: str) -> str:
    """Quote a source column name for use in SQL.

    FragHub emits at least one column whose name contains a space (``NUM PEAKS``),
    so source names cannot be interpolated bare.
    """
    escaped = column.replace('"', '""')
    return f'"{escaped}"'


def nullify(column: str) -> str:
    """SQL mapping a FragHub placeholder in ``column`` to NULL, trimming the rest."""
    tokens = ", ".join(f"'{token}'" for token in MISSING_TOKENS)
    quoted = quote_identifier(column)
    return (
        f"CASE WHEN upper(trim({quoted})) IN ({tokens}) "
        f"THEN NULL ELSE trim({quoted}) END"
    )
