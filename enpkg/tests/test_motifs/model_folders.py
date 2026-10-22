"""Model folders in the layout ``ms2lda_driver.py`` writes, built without MS2LDA."""

import json
from pathlib import Path

import polars as pl

DEFAULT_MOTIFS = [
    {
        "index": 0,
        "name": "motif_0",
        "fragments": [[70.04, 0.2], [91.05, 0.15]],
        "losses": [[162.05, 0.1]],
        "optimized_fragments": [[70.04, 1.0]],
        "optimized_losses": [],
        "candidates": [
            {"smiles": "CC", "inchikey": "OTMSDBZUPAUEDD-UHFFFAOYSA-N", "score": 0.81},
            {"smiles": "not a smiles", "inchikey": None, "score": 0.7},
        ],
    },
    {
        "index": 1,
        "name": "motif_1",
        "fragments": [[105.07, 0.3]],
        "losses": [],
        "optimized_fragments": [],
        "optimized_losses": [],
        "candidates": [],
    },
]

# Feature ids 1-3 of RUN1 match make_analysis(n_spectra=3): precursor m/z 200.0.
DEFAULT_DOCUMENTS = [
    ("spec_0", "RUN1", "1", 200.0),
    ("spec_1", "RUN1", "2", 200.0),
    ("spec_2", "RUN1", "3", 200.0),
    ("spec_3", "RUN1", None, 300.0),
    ("spec_4", "RUN2", "1", 412.2),
]

DEFAULT_MEMBERSHIPS = [
    ("spec_0", "RUN1", "1", 1, 0.05, 0.1),
    ("spec_0", "RUN1", "1", 0, 0.5, 0.7),
    ("spec_1", "RUN1", "2", 1, 0.2, 0.0),
    ("spec_3", "RUN1", None, 0, 0.9, 0.9),
    ("spec_4", "RUN2", "1", 0, 0.9, 0.8),
]


def write_model_folder(
    folder: Path,
    *,
    motifs=None,
    documents=None,
    memberships=None,
    run_names=("RUN1", "RUN2"),
    ionization_mode="pos",
) -> Path:
    """Write a completed model folder with the driver's file names and column types."""
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"motifs": DEFAULT_MOTIFS if motifs is None else motifs}
    (folder / "motifs.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    pl.DataFrame(
        DEFAULT_DOCUMENTS if documents is None else documents,
        schema={"doc_id": pl.String, "run_name": pl.String, "feature_id": pl.String,
                "precursor_mz": pl.Float64},
        orient="row",
    ).write_parquet(folder / "documents.parquet")
    pl.DataFrame(
        DEFAULT_MEMBERSHIPS if memberships is None else memberships,
        schema={"doc_id": pl.String, "run_name": pl.String, "feature_id": pl.String,
                "motif": pl.Int64, "probability": pl.Float64, "overlap": pl.Float64},
        orient="row",
    ).write_parquet(folder / "memberships.parquet")
    manifest = {
        "ionization_mode": ionization_mode,
        "software": {"ms2lda": "2.0.1"},
        "experiments": [{"run_name": name} for name in run_names],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder
