"""Read a model folder written by ``enpkg motifs fit``.

The folder layout is documented in ``enpkg/scripts/ms2lda_driver.py``. A folder is
complete once ``manifest.json`` exists, which the driver writes last.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from enpkg.monolith.data.mass2motif import Mass2Motif, Mass2MotifSet, MotifCandidate

MANIFEST = "manifest.json"
MOTIFS = "motifs.json"
DOCUMENTS = "documents.parquet"
MEMBERSHIPS = "memberships.parquet"


def model_id_of(motifs_json: bytes) -> str:
    """The id of a model: 16 hex digits of blake2b over its ``motifs.json`` bytes.

    The same hash :func:`enpkg.monolith.rdf.uris._short_hash` uses, so a motif IRI
    depends only on the motifs, not on where or when the folder was written.
    """
    return hashlib.blake2b(motifs_json, digest_size=8).hexdigest()


def _motif(record: dict) -> Mass2Motif:
    return Mass2Motif(
        index=record["index"],
        fragments=tuple((float(mz), float(beta)) for mz, beta in record["fragments"]),
        losses=tuple((float(mz), float(beta)) for mz, beta in record["losses"]),
        candidates=tuple(
            MotifCandidate(smiles=c["smiles"], inchikey=c["inchikey"], score=float(c["score"]))
            for c in record["candidates"]
        ),
    )


@dataclass(frozen=True)
class Mass2MotifModel:
    """A fitted model: its motifs and the runs it was trained on."""

    folder: Path
    motif_set: Mass2MotifSet
    run_names: frozenset[str]

    @classmethod
    def load(cls, folder: Path | str) -> "Mass2MotifModel":
        """Read the manifest and motifs of ``folder``.

        Raises:
            FileNotFoundError: ``folder`` has no ``manifest.json``, i.e. it is not a
                completed fit.
        """
        folder = Path(folder)
        manifest_path = folder / MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"{folder} is not a completed Mass2Motif model folder: {MANIFEST} is missing."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        motifs_bytes = (folder / MOTIFS).read_bytes()
        records = json.loads(motifs_bytes)["motifs"]
        motifs = tuple(_motif(record) for record in sorted(records, key=lambda r: r["index"]))
        motif_set = Mass2MotifSet(
            model_id=model_id_of(motifs_bytes),
            ionization_mode=manifest["ionization_mode"],
            software_version=manifest["software"]["ms2lda"],
            motifs=motifs,
        )
        run_names = frozenset(exp["run_name"] for exp in manifest["experiments"])
        return cls(folder=folder, motif_set=motif_set, run_names=run_names)

    def documents(self, run_name: str) -> pl.DataFrame:
        """The training documents of ``run_name``: ``doc_id, feature_id, precursor_mz``.

        ``feature_id`` is cast to an integer, the type ``AnnotatedSpectrum.feature_id``
        returns; a document whose spectrum had no feature id is dropped.
        """
        return (
            pl.scan_parquet(self.folder / DOCUMENTS)
            .filter(pl.col("run_name") == run_name)
            .drop_nulls("feature_id")
            .select("doc_id", pl.col("feature_id").cast(pl.Int64), "precursor_mz")
            .collect()
        )

    def memberships(self, run_name: str) -> pl.DataFrame:
        """The memberships of ``run_name``: ``feature_id, motif, probability, overlap``."""
        return (
            pl.scan_parquet(self.folder / MEMBERSHIPS)
            .filter(pl.col("run_name") == run_name)
            .drop_nulls("feature_id")
            .select(pl.col("feature_id").cast(pl.Int64), "motif", "probability", "overlap")
            .collect()
        )
