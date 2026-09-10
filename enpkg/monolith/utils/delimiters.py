"""Column-separator detection, shared by every delimited-text reader."""

from pathlib import Path

_CANDIDATE_SEPARATORS = (",", ";", "\t")


def sniff_separator(path: Path) -> str:
    """Detect the column separator from the first non-empty line of a CSV/TSV file.

    Picks whichever of comma / semicolon / tab appears most often in the header
    row. Raises if none are present, so a malformed file fails loudly instead of
    degrading to a single-column read where the only "column" name is the entire
    header glued together.

    A file extension is not evidence of its delimiter: FragHub exports are
    tab-separated but named ``.csv``, which is why detection happens here rather
    than being assumed at each call site.

    Raises:
        ValueError: If the file is empty or its header contains none of the
            candidate separators.
    """
    with Path(path).open("r", encoding="utf-8") as handle:
        first_line = ""
        for line in handle:
            if line.strip():
                first_line = line
                break
    if not first_line:
        raise ValueError(f"{path} is empty")

    counts = {sep: first_line.count(sep) for sep in _CANDIDATE_SEPARATORS}
    separator, count = max(counts.items(), key=lambda kv: kv[1])
    if count == 0:
        raise ValueError(
            f"Could not detect a column separator in {path}: the header line "
            "contains no commas, semicolons, or tabs."
        )
    return separator
