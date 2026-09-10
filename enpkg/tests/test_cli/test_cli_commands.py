"""End-to-end tests for the ``enpkg`` subcommands that do not run the pipeline.

These go through Typer's runner, so they exercise argument parsing, the config-loading
path shared with the GUI, and the exit codes a shell script or CI job would branch on.
"""

import yaml
from typer.testing import CliRunner

from enpkg.cli.main import app
from enpkg.monolith.pipeline.blocks import BLOCKS
from enpkg.monolith.pipeline.config_io import SELECTION_KEY, SERIALIZER_KEY

runner = CliRunner()

# MSEnhancerConfig is the only block config with a field that has no default, so a
# generated template needs exactly this one value before it validates.
_REQUIRED_FILL = ("ms_enhancer", "duckdb_path", "enpkg.duckdb")


def _init(tmp_path, blocks: str | None = None, fill: bool = True):
    """Generate a config template, optionally filling the one required field."""
    out = tmp_path / "config.yaml"
    args = ["config", "init", "--out", str(out), "--force"]
    if blocks:
        args += ["--blocks", blocks]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output

    if fill:
        data = yaml.safe_load(out.read_text())
        section, field, value = _REQUIRED_FILL
        if section in data:
            data[section][field] = value
            out.write_text(yaml.safe_dump(data, sort_keys=False))
    return out


def test_blocks_list_names_every_registered_block():
    result = runner.invoke(app, ["blocks", "list"])
    assert result.exit_code == 0
    for block in BLOCKS:
        assert block.id in result.output


def test_config_init_writes_every_block_by_default(tmp_path):
    out = _init(tmp_path, fill=False)
    data = yaml.safe_load(out.read_text())
    assert data[SELECTION_KEY] == [b.id for b in BLOCKS]
    assert SERIALIZER_KEY in data


def test_config_init_reports_fields_with_no_default(tmp_path):
    out = tmp_path / "c.yaml"
    result = runner.invoke(app, ["config", "init", "--out", str(out), "--force"])
    assert "ms_enhancer.duckdb_path" in result.output


def test_config_init_refuses_to_clobber(tmp_path):
    out = _init(tmp_path)
    result = runner.invoke(app, ["config", "init", "--out", str(out)])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_config_init_rejects_unknown_blocks(tmp_path):
    result = runner.invoke(
        app, ["config", "init", "--out", str(tmp_path / "c.yaml"), "--blocks", "nope"]
    )
    assert result.exit_code == 1
    assert "Unknown block ids" in result.output


def test_config_validate_accepts_a_filled_template(tmp_path):
    out = _init(tmp_path)
    result = runner.invoke(app, ["config", "validate", str(out)])
    assert result.exit_code == 0, result.output
    assert "is valid" in result.output


def test_config_validate_rejects_an_unfilled_template(tmp_path):
    """A template is not yet a runnable config, and must not claim to be."""
    out = _init(tmp_path, fill=False)
    result = runner.invoke(app, ["config", "validate", str(out)])
    assert result.exit_code == 1
    assert "duckdb_path" in result.output


def test_config_validate_reports_a_missing_file(tmp_path):
    result = runner.invoke(app, ["config", "validate", str(tmp_path / "absent.yaml")])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_config_validate_refuses_a_file_without_a_selection(tmp_path):
    """The section names cannot stand in for the selection, so guessing is refused."""
    out = tmp_path / "legacy.yaml"
    out.write_text(yaml.safe_dump({"network": {}}))
    result = runner.invoke(app, ["config", "validate", str(out)])
    assert result.exit_code == 1
    assert SELECTION_KEY in result.output


def test_config_validate_warns_about_unmet_dependencies(tmp_path):
    """weights needs network; selecting it alone runs nothing and should say so."""
    out = _init(tmp_path, blocks="weights,ms1")
    result = runner.invoke(app, ["config", "validate", str(out)])
    assert result.exit_code == 0
    assert "weights needs network" in result.output


def test_config_show_prints_resolved_values(tmp_path):
    out = _init(tmp_path, blocks="network")
    result = runner.invoke(app, ["config", "show", str(out)])
    assert result.exit_code == 0
    shown = yaml.safe_load(result.output)
    assert shown[SELECTION_KEY] == ["network"]
    # Defaults the file never mentioned are filled in by validation.
    assert "mn_top_n" in shown["network"]


def test_batch_discover_lists_experiments(tmp_path):
    parent = tmp_path / "batch"
    (parent).mkdir()
    (parent / "metadata.tsv").write_text("sample\n")
    for name in ("exp_a", "exp_b"):
        sub = parent / name
        sub.mkdir()
        (sub / f"{name}.mgf").write_text("")
        (sub / f"{name}_quant.csv").write_text("")

    result = runner.invoke(app, ["batch", "discover", "-p", str(parent)])
    assert result.exit_code == 0, result.output
    assert "2 experiment(s)" in result.output
    assert "exp_a" in result.output and "exp_b" in result.output


def test_batch_discover_flags_a_sirius_sibling(tmp_path):
    parent = tmp_path / "batch"
    sub = parent / "exp"
    sub.mkdir(parents=True)
    (parent / "metadata.tsv").write_text("sample\n")
    (sub / "exp.mgf").write_text("")
    (sub / "exp_quant.csv").write_text("")
    (sub / "exp_sirius.mgf").write_text("")

    result = runner.invoke(app, ["batch", "discover", "-p", str(parent)])
    assert "+sirius" in result.output


def test_batch_discover_fails_on_an_empty_parent(tmp_path):
    parent = tmp_path / "batch"
    parent.mkdir()
    result = runner.invoke(app, ["batch", "discover", "-p", str(parent)])
    assert result.exit_code == 1
    assert "No experiment subfolders" in result.output


def test_run_requires_input_files(tmp_path):
    out = _init(tmp_path, blocks="network")
    result = runner.invoke(app, ["run", "--config", str(out)])
    assert result.exit_code == 1
    assert "spectra" in result.output


def test_input_discovery_prefers_txt_metadata_over_a_csv_quant(tmp_path):
    """Metadata accepts .tsv/.txt/.csv while quant tables are .csv, so the two overlap.

    A folder holding `study_quant.csv` and `metadata.txt` must resolve metadata to the
    .txt: matching on "any accepted suffix" would pick whichever file sorts first, which
    for real folders is the quant table, and the run would then fail deep inside the
    loader looking for a metadata column in a quant file.
    """
    from enpkg.cli.run import _resolve_inputs

    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "study.mgf").write_text("")
    (folder / "study_quant.csv").write_text("")
    (folder / "metadata.txt").write_text("")

    spectra, metadata, quant = _resolve_inputs(folder, None, None, None)
    assert spectra.name == "study.mgf"
    assert metadata.name == "metadata.txt"
    assert quant.name == "study_quant.csv"


def test_explicit_paths_are_not_replaced_by_discovery(tmp_path):
    from enpkg.cli.run import _resolve_inputs

    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "a.mgf").write_text("")
    (folder / "z.mgf").write_text("")
    (folder / "meta.tsv").write_text("")
    (folder / "q.csv").write_text("")

    _, _, _ = _resolve_inputs(folder, None, None, None)
    spectra, _, _ = _resolve_inputs(folder, folder / "z.mgf", None, None)
    assert spectra.name == "z.mgf"
