"""`enpkg motifs fit`: experiment selection, the request, and how the driver is run.

The MS2LDA environment is never started here: ``run_check`` and ``run_fit`` are
replaced, and the fit's output is a model folder written by ``write_model_folder``.
"""

import json
import sys
from pathlib import Path

import pytest

from enpkg.scripts import fit_mass2motifs
from enpkg.scripts.fit_mass2motifs import (
    DRIVER,
    FitError,
    build_request,
    driver_command,
    resolve_interpreter,
    select_experiments,
)
from enpkg.tests.test_motifs.model_folders import write_model_folder


def _batch(tmp_path: Path, experiments: dict[str, str | None], suffix: str = ".mgf") -> Path:
    """A batch folder: one subfolder per run, and metadata giving each run's sample_type.

    A ``None`` sample_type leaves the run out of the metadata file.
    """
    batch = tmp_path / "batch"
    batch.mkdir()
    rows = ["sample_id\tsample_type\tsample_filename_pos"]
    for run, sample_type in experiments.items():
        sub = batch / run
        sub.mkdir()
        (sub / f"{run}{suffix}").write_text("", encoding="utf-8")
        (sub / f"{run}_quant.csv").write_text("", encoding="utf-8")
        if sample_type is not None:
            rows.append(f"S_{run}\t{sample_type}\t{run}.mzML")
    (batch / "metadata.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return batch


def test_blanks_qcs_and_runs_missing_from_the_metadata_are_left_out(tmp_path):
    batch = _batch(tmp_path, {"a": "sample", "b": "QC", "c": None, "d": "sample"})
    assert [exp.run_name for exp in select_experiments(batch, "pos")] == ["a", "d"]


def test_run_names_restrict_the_fit(tmp_path):
    batch = _batch(tmp_path, {"a": "sample", "b": "sample"})
    assert [exp.run_name for exp in select_experiments(batch, "pos", ["b"])] == ["b"]


def test_an_unknown_run_name_fails_before_fitting(tmp_path):
    batch = _batch(tmp_path, {"a": "sample"})
    with pytest.raises(FitError, match="typo"):
        select_experiments(batch, "pos", ["a", "typo"])


def test_spectra_files_ms2lda_cannot_read_fail_before_fitting(tmp_path):
    batch = _batch(tmp_path, {"a": "sample"}, suffix=".mzxml")
    with pytest.raises(FitError, match="a.mzxml"):
        select_experiments(batch, "pos")


def test_a_batch_with_only_blanks_has_nothing_to_fit(tmp_path):
    batch = _batch(tmp_path, {"a": "blank"})
    with pytest.raises(FitError, match="No experiment left"):
        select_experiments(batch, "pos")


def test_the_request_lists_absolute_spectra_paths(tmp_path):
    batch = _batch(tmp_path, {"a": "sample"})
    request = build_request(select_experiments(batch, "pos"), tmp_path / "model", "pos", 20, 100, True, False, {})
    assert request["experiments"] == [
        {"run_name": "a", "spectra_path": str((batch / "a" / "a.mgf").resolve())}
    ]
    assert request["out_dir"] == str((tmp_path / "model").resolve())
    assert (request["n_motifs"], request["n_iterations"], request["mag"], request["viz"]) == (20, 100, True, False)


def test_the_interpreter_comes_from_the_option_then_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MS2LDA_PYTHON", sys.executable)
    assert resolve_interpreter(None) == Path(sys.executable)
    monkeypatch.delenv("MS2LDA_PYTHON")
    with pytest.raises(FitError, match="--ms2lda-python"):
        resolve_interpreter(None)
    with pytest.raises(FitError, match="not found"):
        resolve_interpreter(str(tmp_path / "missing.exe"))


def test_the_driver_command_runs_the_driver_file():
    python, request = Path("py.exe"), Path("m/request.json")
    assert driver_command(python, request, check=True) == ["py.exe", str(DRIVER), "--check", str(Path("m/request.json"))]
    assert driver_command(python, request, check=False) == ["py.exe", str(DRIVER), str(Path("m/request.json"))]


@pytest.fixture
def fake_driver(monkeypatch):
    """Replace both driver runs; the fit writes a model folder. Returns the calls made."""
    calls = []

    def run_check(python, request_path):
        calls.append(("check", json.loads(request_path.read_text(encoding="utf-8"))))
        return {"versions": {"ms2lda": "2.0.1"}, "problems": []}

    def run_fit(python, request_path):
        calls.append(("fit", None))
        write_model_folder(request_path.parent)
        return 0

    monkeypatch.setattr(fit_mass2motifs, "run_check", run_check)
    monkeypatch.setattr(fit_mass2motifs, "run_fit", run_fit)
    return calls


def _argv(batch, out, *extra):
    return [str(batch), "--out", str(out), "--n-motifs", "20", "--n-iterations", "100",
            "--ms2lda-python", sys.executable, *extra]


def test_main_checks_the_environment_then_fits(tmp_path, fake_driver):
    batch = _batch(tmp_path, {"a": "sample"})
    fit_mass2motifs.main(_argv(batch, tmp_path / "model", "--ionization-mode", "pos"))
    assert [name for name, _ in fake_driver] == ["check", "fit"]
    request = fake_driver[0][1]
    assert request["mag"] is True
    assert request["viz"] is False
    assert (tmp_path / "model" / "request.json").is_file()


def test_viz_asks_the_driver_for_ms2lda_visualisation_files(tmp_path, fake_driver):
    batch = _batch(tmp_path, {"a": "sample"})
    fit_mass2motifs.main(_argv(batch, tmp_path / "model", "--ionization-mode", "pos", "--viz"))
    assert fake_driver[0][1]["viz"] is True


def test_negative_mode_fits_without_mag(tmp_path, fake_driver):
    batch = tmp_path / "batch"
    _batch(tmp_path, {"a": "sample"})
    metadata = batch / "metadata.tsv"
    metadata.write_text(metadata.read_text().replace("sample_filename_pos", "sample_filename_neg"))
    fit_mass2motifs.main(_argv(batch, tmp_path / "model", "--ionization-mode", "neg"))
    assert fake_driver[0][1]["mag"] is False


def test_the_user_ms2lda_config_reaches_the_request(tmp_path, fake_driver):
    batch = _batch(tmp_path, {"a": "sample"})
    config = tmp_path / "ms2lda.json"
    config.write_text(json.dumps({"model_parameters": {"alpha": 0.1}}))
    fit_mass2motifs.main(_argv(batch, tmp_path / "model", "--ionization-mode", "pos",
                               "--ms2lda-config", str(config), "--no-mag"))
    request = fake_driver[0][1]
    assert request["ms2lda_config"] == {"model_parameters": {"alpha": 0.1}}
    assert request["mag"] is False


def test_a_non_empty_model_folder_is_refused(tmp_path, fake_driver):
    batch = _batch(tmp_path, {"a": "sample"})
    out = tmp_path / "model"
    out.mkdir()
    (out / "old.txt").write_text("x")
    with pytest.raises(SystemExit):
        fit_mass2motifs.main(_argv(batch, out, "--ionization-mode", "pos"))
    assert fake_driver == []


def test_a_failed_fit_exits_with_an_error(tmp_path, monkeypatch, fake_driver):
    monkeypatch.setattr(fit_mass2motifs, "run_fit", lambda python, request_path: 1)
    batch = _batch(tmp_path, {"a": "sample"})
    with pytest.raises(SystemExit):
        fit_mass2motifs.main(_argv(batch, tmp_path / "model", "--ionization-mode", "pos"))


def test_check_problems_stop_the_fit(tmp_path, monkeypatch):
    def run_check(python, request_path):
        raise FitError("ms2lda 2.0.0 is installed")

    monkeypatch.setattr(fit_mass2motifs, "run_check", run_check)
    monkeypatch.setattr(fit_mass2motifs, "run_fit", lambda *args: pytest.fail("fit started"))
    batch = _batch(tmp_path, {"a": "sample"})
    with pytest.raises(SystemExit):
        fit_mass2motifs.main(_argv(batch, tmp_path / "model", "--ionization-mode", "pos"))
