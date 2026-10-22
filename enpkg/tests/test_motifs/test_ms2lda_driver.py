"""The parts of ms2lda_driver.py that run without MS2LDA.

The driver runs under the MS2LDA environment's interpreter (Python 3.11/3.12), which
has neither enpkg nor this project's Python, so its source is checked for both.
"""

import ast
import copy

import numpy as np
import pytest

from enpkg.scripts import ms2lda_driver as driver
from enpkg.scripts.fit_mass2motifs import DRIVER


class _Spectrum:
    """The ``get`` interface of a matchms Spectrum."""

    def __init__(self, **metadata):
        self.metadata = metadata

    def get(self, key, default=None):
        return self.metadata.get(key, default)


class _Fragments:
    def __init__(self, mz, intensities):
        self.mz = np.array(mz)
        self.intensities = np.array(intensities)


class _Motif:
    def __init__(self, peaks, losses):
        self.peaks = peaks
        self.losses = losses


DEFAULTS = {
    "dataset_parameters": {"acquisition_type": "DDA", "charge": 1, "significant_digits": 2,
                           "name": "ms2lda_cli_run", "output_folder": "ms2lda_results"},
    "model_parameters": {"alpha": 0.6, "eta": 0.01, "seed": 42},
    "annotation_parameters": {
        "criterium": "biggest",
        "s2v_model_path": "../MS2LDA/x/150225_Spec2Vec_pos_CleanedLibraries.model",
        "s2v_library_embeddings": "../MS2LDA/x/150225_CleanedLibraries_Spec2Vec_pos_embeddings.npy",
        "s2v_library_db": "../MS2LDA/x/150225_CombLibraries_spectra.db",
    },
}


def _request(**overrides):
    request = {"out_dir": "/models/m1", "ionization_mode": "pos", "ms2lda_config": {}}
    request.update(overrides)
    return request


def test_the_driver_parses_as_python_3_11():
    ast.parse(DRIVER.read_text(encoding="utf-8"), feature_version=(3, 11))


def test_the_driver_does_not_import_enpkg():
    tree = ast.parse(DRIVER.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not {name for name in imported if name.split(".")[0] == "enpkg"}


def test_merged_parameters_leave_the_defaults_untouched():
    defaults = copy.deepcopy(DEFAULTS)
    driver.merged_parameters(defaults, _request(ms2lda_config={"model_parameters": {"alpha": 0.1}}), "/s2v")
    assert defaults == DEFAULTS


def test_user_values_are_merged_key_by_key():
    params = driver.merged_parameters(
        DEFAULTS, _request(ms2lda_config={"model_parameters": {"alpha": 0.1}}), "/s2v"
    )
    assert params["model_parameters"] == {"alpha": 0.1, "eta": 0.01, "seed": 42}


def test_the_request_sets_the_output_folder_name_and_charge(tmp_path):
    params = driver.merged_parameters(DEFAULTS, _request(ionization_mode="neg"), "/s2v")
    dataset = params["dataset_parameters"]
    assert dataset["output_folder"].replace("\\", "/") == "/models/m1/ms2lda"
    assert dataset["name"] == "m1"
    assert dataset["charge"] == -1


def test_a_charge_in_the_user_config_is_kept():
    params = driver.merged_parameters(
        DEFAULTS, _request(ionization_mode="neg", ms2lda_config={"dataset_parameters": {"charge": 2}}), "/s2v"
    )
    assert params["dataset_parameters"]["charge"] == 2


def test_spec2vec_paths_point_into_the_download_folder_unless_given():
    params = driver.merged_parameters(
        DEFAULTS,
        _request(ms2lda_config={"annotation_parameters": {"s2v_library_db": "/mine/lib.db"}}),
        "/s2v",
    )
    annotation = {k: v.replace("\\", "/") for k, v in params["annotation_parameters"].items()}
    assert annotation["s2v_model_path"] == "/s2v/150225_Spec2Vec_pos_CleanedLibraries.model"
    assert annotation["s2v_library_embeddings"] == "/s2v/150225_CleanedLibraries_Spec2Vec_pos_embeddings.npy"
    assert annotation["s2v_library_db"] == "/mine/lib.db"


def test_missing_spec2vec_files_are_listed(tmp_path):
    present = tmp_path / "model.model"
    present.write_text("")
    annotation = {"s2v_model_path": str(present), "s2v_library_embeddings": str(tmp_path / "e.npy"),
                  "s2v_library_db": str(tmp_path / "l.db")}
    assert driver.missing_spec2vec_files(annotation) == [str(tmp_path / "e.npy"), str(tmp_path / "l.db")]


def test_spectra_must_be_numbered_in_document_order():
    driver.check_spectrum_ids([_Spectrum(id="spec_0"), _Spectrum(id="spec_1")])
    with pytest.raises(RuntimeError, match="by position"):
        driver.check_spectrum_ids([_Spectrum(id="spec_0"), _Spectrum(id="spec_2")])


def test_documents_carry_the_run_name_and_feature_id_of_their_spectrum():
    rows = driver.document_rows([
        _Spectrum(enpkg_run_name="A", feature_id="12", precursor_mz=301.1),
        _Spectrum(enpkg_run_name="B", precursor_mz=np.float64(250.0)),
    ])
    assert rows == [
        {"doc_id": "spec_0", "run_name": "A", "feature_id": "12", "precursor_mz": 301.1},
        {"doc_id": "spec_1", "run_name": "B", "feature_id": None, "precursor_mz": 250.0},
    ]


def test_memberships_pair_theta_with_overlap():
    documents = driver.document_rows([
        _Spectrum(enpkg_run_name="A", feature_id="12", precursor_mz=301.1),
        _Spectrum(enpkg_run_name="A", feature_id="13", precursor_mz=310.0),
    ])
    lda_dict = {
        "theta": {"spec_0": {"motif_3": 0.6, "motif_10": 0.3}, "spec_1": {}},
        "overlap_scores": {"spec_0": {"motif_3": 0.8}, "spec_1": {"motif_3": 0.2}},
    }
    rows = driver.membership_rows(lda_dict, documents)
    assert rows == [
        {"doc_id": "spec_0", "run_name": "A", "feature_id": "12", "motif": 3, "probability": 0.6, "overlap": 0.8},
        {"doc_id": "spec_0", "run_name": "A", "feature_id": "12", "motif": 10, "probability": 0.3, "overlap": 0.0},
    ]


def test_motif_records_without_mag():
    records = driver.motif_records(
        [[("frag@70.04", np.float32(0.25)), ("loss@162.05", 0.1)]], None, None, None, lambda s: None
    )
    assert records == [{
        "index": 0, "name": "motif_0",
        "fragments": [[70.04, 0.25]], "losses": [[162.05, pytest.approx(0.1)]],
        "optimized_fragments": [], "optimized_losses": [], "candidates": [],
    }]


def test_motif_records_with_mag():
    optimized = [_Motif(_Fragments([70.04], [1.0]), _Fragments([], []))]
    records = driver.motif_records(
        [[("frag@70.04", 0.25)]], optimized, [["CC", "bad"]], [[np.float32(0.9), 0.5]],
        lambda smiles: "OTMSDBZUPAUEDD-UHFFFAOYSA-N" if smiles == "CC" else None,
    )
    record = records[0]
    assert record["optimized_fragments"] == [[70.04, 1.0]]
    assert record["optimized_losses"] == []
    assert record["candidates"] == [
        {"smiles": "CC", "inchikey": "OTMSDBZUPAUEDD-UHFFFAOYSA-N", "score": pytest.approx(0.9)},
        {"smiles": "bad", "inchikey": None, "score": 0.5},
    ]


def test_json_is_written_with_sorted_keys(tmp_path):
    driver.write_json(tmp_path / "a.json", {"b": 1, "a": [1.5]})
    driver.write_json(tmp_path / "b.json", {"a": [1.5], "b": 1})
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
