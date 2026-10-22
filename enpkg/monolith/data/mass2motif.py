"""Mass2Motifs and their memberships, as read from an MS2LDA model folder.

A Mass2Motif is one topic of an LDA model fitted by MS2LDA over a corpus of MS2
spectra: a probability distribution over fragment and neutral-loss m/z bins. Each
spectrum of the corpus is a mixture of motifs. Motifs are comparable only within the
model that produced them, which ``Mass2MotifSet.model_id`` identifies.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class MotifCandidate:
    """A library molecule MAG proposes as containing the motif's substructure.

    ``score`` is the Spec2Vec cosine similarity between the motif and the library
    spectrum of the molecule. ``inchikey`` is None when RDKit could not parse
    ``smiles``.
    """

    smiles: str
    inchikey: Optional[str]
    score: float


@dataclass(frozen=True, slots=True)
class Mass2Motif:
    """One motif: its top fragment and loss bins with their topic-word probability.

    ``fragments`` and ``losses`` hold ``(m/z, beta)`` pairs, beta being the
    probability of the bin under the motif. ``candidates`` is empty when MAG did not
    run or found no cluster of library hits.
    """

    index: int
    fragments: tuple[tuple[float, float], ...]
    losses: tuple[tuple[float, float], ...]
    candidates: tuple[MotifCandidate, ...] = ()

    @property
    def name(self) -> str:
        """MS2LDA's name for the motif, ``motif_<index>``."""
        return f"motif_{self.index}"


@dataclass(frozen=True, slots=True)
class Mass2MotifSet:
    """Every motif of one fitted model.

    ``model_id`` is a hash of the model folder's ``motifs.json``, so two folders with
    the same motifs share it. ``motifs[i].index == i``.
    """

    model_id: str
    ionization_mode: str
    software_version: str
    motifs: tuple[Mass2Motif, ...]


@dataclass(frozen=True, slots=True)
class MotifMembership:
    """The part of one feature's spectrum the model attributes to one motif.

    ``probability`` is the document-topic probability theta from the training run;
    ``overlap`` is MS2LDA's overlap score, the sum over the motif's bins of beta times
    the fraction of the spectrum's copies of that bin assigned to the motif.
    """

    motif_index: int
    probability: float
    overlap: float
