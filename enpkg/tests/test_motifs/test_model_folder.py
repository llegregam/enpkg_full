"""Reading a model folder written by `enpkg motifs fit`."""

import hashlib

import pytest

from enpkg.monolith.loaders.mass2motif_model import Mass2MotifModel, model_id_of
from enpkg.tests.test_motifs.model_folders import DEFAULT_MOTIFS, write_model_folder


def test_a_folder_without_a_manifest_is_not_a_model(model_folder):
    (model_folder / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        Mass2MotifModel.load(model_folder)


def test_motifs_are_read_in_index_order_with_their_candidates(tmp_path):
    folder = write_model_folder(tmp_path / "m", motifs=list(reversed(DEFAULT_MOTIFS)))
    motif_set = Mass2MotifModel.load(folder).motif_set
    assert [motif.index for motif in motif_set.motifs] == [0, 1]
    first = motif_set.motifs[0]
    assert first.name == "motif_0"
    assert first.fragments == ((70.04, 0.2), (91.05, 0.15))
    assert first.losses == ((162.05, 0.1),)
    assert [c.inchikey for c in first.candidates] == ["OTMSDBZUPAUEDD-UHFFFAOYSA-N", None]
    assert motif_set.motifs[1].candidates == ()
    assert motif_set.software_version == "2.0.1"
    assert motif_set.ionization_mode == "pos"


def test_the_model_id_is_the_hash_of_motifs_json(model_folder):
    model = Mass2MotifModel.load(model_folder)
    expected = hashlib.blake2b((model_folder / "motifs.json").read_bytes(), digest_size=8).hexdigest()
    assert model.motif_set.model_id == expected == model_id_of((model_folder / "motifs.json").read_bytes())


def test_the_model_id_depends_only_on_the_motifs(tmp_path):
    a = write_model_folder(tmp_path / "a")
    b = write_model_folder(tmp_path / "b", run_names=("OTHER",), memberships=[])
    changed = [dict(DEFAULT_MOTIFS[0], fragments=[[70.05, 0.2]]), DEFAULT_MOTIFS[1]]
    c = write_model_folder(tmp_path / "c", motifs=changed)
    ids = [Mass2MotifModel.load(folder).motif_set.model_id for folder in (a, b, c)]
    assert ids[0] == ids[1] != ids[2]


def test_run_names_come_from_the_manifest(model_folder):
    assert Mass2MotifModel.load(model_folder).run_names == frozenset({"RUN1", "RUN2"})


def test_memberships_are_filtered_to_one_run_with_integer_feature_ids(model_folder):
    memberships = Mass2MotifModel.load(model_folder).memberships("RUN1")
    assert memberships.columns == ["feature_id", "motif", "probability", "overlap"]
    assert sorted(memberships.rows()) == [(1, 0, 0.5, 0.7), (1, 1, 0.05, 0.1), (2, 1, 0.2, 0.0)]


def test_documents_without_a_feature_id_are_dropped(model_folder):
    documents = Mass2MotifModel.load(model_folder).documents("RUN1")
    assert documents["feature_id"].to_list() == [1, 2, 3]
    assert documents["precursor_mz"].to_list() == [200.0, 200.0, 200.0]
