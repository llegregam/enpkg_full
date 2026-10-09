"""
Fit an MS2LDA 2.0 model on the experiments of a batch folder.

The model folder this writes is what the ``mass2motif`` pipeline block reads: the
Mass2Motifs, and the motif memberships (document-topic probability and overlap score)
of every spectrum the model was trained on, keyed by run name and feature id.

MS2LDA runs unmodified in its own Python environment, where ``ms2lda`` 2.0.1 is
installed (it requires Python < 3.13). This script writes ``request.json`` into the
model folder and runs ``ms2lda_driver.py`` with that environment's interpreter, first
with ``--check`` (versions and MAG resources), then for the fit.

Usage
-----
enpkg motifs fit path/to/batch \\
    --out          path/to/model_folder \\
    --n-motifs     200 \\
    --n-iterations 1000 \\
    --ionization-mode pos \\
    --ms2lda-python path/to/envs/ms2lda/python.exe

Experiments are found and selected as ``enpkg batch`` finds and selects them: one
subfolder per experiment, the shared metadata file at the top of the batch folder,
``--experiments-file`` to restrict the run names. Runs the metadata marks as not a
biological sample (blanks, QCs), and runs the metadata does not list, are left out.
``--ms2lda-config`` takes a JSON file in the format of MS2LDA's
``example_config.json``; its values are merged over MS2LDA's own CLI defaults.
``--viz`` adds MS2LDA's figures and MS2LDAViz file to the model folder's ``ms2lda/``.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

DRIVER = Path(__file__).with_name("ms2lda_driver.py")
INTERPRETER_ENV_VAR = "MS2LDA_PYTHON"
# Spectra formats both the pipeline and MS2LDA's loader read.
FIT_SPECTRA_SUFFIXES = (".mgf", ".mzml")

logger = logging.getLogger(__name__)


class FitError(Exception):
    """A problem with the fit's inputs, reported before MS2LDA is started."""


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def select_experiments(
    batch_dir: Path,
    ionization_mode: str,
    run_names: Optional[list[str]] = None,
) -> list:
    """The experiments of ``batch_dir`` the fit trains on.

    Discovery, ``run_names`` selection and the sample/QC rule are those of
    :func:`enpkg.monolith.pipeline.batch_runner.run_batch`.

    Raises:
        FitError: no shared metadata, no experiments, unknown run names, a spectra
            format MS2LDA does not read, or no experiment left after exclusions.
    """
    from enpkg.monolith.loaders.analysis_loader import AnalysisLoader
    from enpkg.monolith.pipeline.batch_runner import (
        _select_experiments,
        discover_experiments,
        find_shared_metadata,
    )

    metadata_path = find_shared_metadata(batch_dir)
    if metadata_path is None:
        raise FitError(f"No shared metadata file found at {batch_dir}")
    experiments = discover_experiments(batch_dir)
    if not experiments:
        raise FitError(f"No experiment subfolders with spectra + quant found under {batch_dir}")
    if run_names is not None:
        experiments, unknown = _select_experiments(experiments, run_names)
        if unknown:
            raise FitError(f"Unknown experiment(s) under {batch_dir}: {', '.join(unknown)}")

    unreadable = [exp.spectra_path.name for exp in experiments
                  if exp.spectra_path.suffix.lower() not in FIT_SPECTRA_SUFFIXES]
    if unreadable:
        raise FitError(
            f"MS2LDA reads {', '.join(FIT_SPECTRA_SUFFIXES)} spectra files, not: {', '.join(unreadable)}"
        )

    kept = []
    for exp in experiments:
        try:
            metadata = AnalysisLoader._load_metadata(metadata_path, ionization_mode, exp.run_name)
        except ValueError as exc:
            logger.warning("Leaving out %s: %s", exp.run_name, exc)
            continue
        if not metadata.is_sample():
            logger.info("Leaving out %s: sample_type=%r is not a biological sample",
                        exp.run_name, metadata.sample_type)
            continue
        kept.append(exp)
    if not kept:
        raise FitError("No experiment left to fit on.")
    return kept


def build_request(
    experiments: list,
    out_dir: Path,
    ionization_mode: str,
    n_motifs: int,
    n_iterations: int,
    mag: bool,
    viz: bool,
    ms2lda_config: dict,
) -> dict:
    """The ``request.json`` content ``ms2lda_driver.py`` reads."""
    return {
        "out_dir": str(out_dir.resolve()),
        "ionization_mode": ionization_mode,
        "n_motifs": n_motifs,
        "n_iterations": n_iterations,
        "mag": mag,
        "viz": viz,
        "ms2lda_config": ms2lda_config,
        "experiments": [
            {"run_name": exp.run_name, "spectra_path": str(exp.spectra_path.resolve())}
            for exp in experiments
        ],
    }


def resolve_interpreter(option: Optional[str]) -> Path:
    """The MS2LDA environment's Python, from the option or ``MS2LDA_PYTHON``."""
    raw = option or os.environ.get(INTERPRETER_ENV_VAR, "")
    if not raw.strip():
        raise FitError(
            f"No MS2LDA interpreter given: pass --ms2lda-python or set {INTERPRETER_ENV_VAR}."
        )
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FitError(f"MS2LDA interpreter not found: {path}")
    return path


def driver_command(python: Path, request_path: Path, check: bool) -> list[str]:
    """The command line that runs the driver on ``request_path``."""
    command = [str(python), str(DRIVER)]
    if check:
        command.append("--check")
    command.append(str(request_path))
    return command


def _driver_env() -> dict:
    # The GUI and log readers decode the driver's output as UTF-8; a Windows
    # interpreter writing to a pipe otherwise encodes in the ANSI code page.
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run_check(python: Path, request_path: Path) -> dict:
    """Run the driver's ``--check`` and return its report.

    Raises:
        FitError: the driver could not run, or reported problems.
    """
    completed = subprocess.run(
        driver_command(python, request_path, check=True),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=_driver_env(),
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        report = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        raise FitError(
            f"The MS2LDA environment could not run the driver (exit code {completed.returncode}):\n"
            f"{completed.stderr.strip()}"
        ) from None
    if report["problems"]:
        raise FitError("\n".join(report["problems"]))
    return report


def run_fit(python: Path, request_path: Path) -> int:
    """Run the fit with the driver's output on this process's console; return its exit code."""
    return subprocess.run(driver_command(python, request_path, check=False), env=_driver_env()).returncode


def main(argv: list[str] | None = None, prog: str | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Fit an MS2LDA 2.0 model on the experiments of a batch folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("batch_dir", type=Path, help="Batch folder, laid out as for `enpkg batch`")
    parser.add_argument("--out", required=True, type=Path,
                        help="Model folder to create; must not exist or be empty")
    parser.add_argument("--n-motifs", required=True, type=int, help="Number of Mass2Motifs (LDA topics)")
    parser.add_argument("--n-iterations", required=True, type=int,
                        help="Maximum number of LDA training iterations")
    parser.add_argument("--ionization-mode", required=True, choices=("pos", "neg"),
                        help="Ionization mode of the experiments")
    parser.add_argument("--experiments-file", type=Path,
                        help="Text file with one run name per line; only these experiments are fitted")
    parser.add_argument("--ms2lda-python",
                        help="Python interpreter of the MS2LDA environment; "
                        f"falls back to the {INTERPRETER_ENV_VAR} environment variable")
    parser.add_argument("--ms2lda-config", type=Path,
                        help="MS2LDA parameter JSON, merged over MS2LDA's CLI defaults")
    parser.add_argument("--no-mag", action="store_true",
                        help="Skip Motif Annotation Guidance (no candidate structures)")
    parser.add_argument("--viz", action="store_true",
                        help="Also write MS2LDA's figures, motif network and MS2LDAViz file "
                        "(fewer than 500 motifs only); slow on large fits")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG-level logging")
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    from enpkg.monolith.loaders.mass2motif_model import Mass2MotifModel
    from enpkg.monolith.pipeline.batch_runner import read_experiment_list

    try:
        if args.out.exists() and any(args.out.iterdir()):
            raise FitError(f"{args.out} is not empty; choose a new model folder.")
        if args.n_motifs < 1 or args.n_iterations < 1:
            raise FitError("--n-motifs and --n-iterations must be positive.")
        python = resolve_interpreter(args.ms2lda_python)
        ms2lda_config = {}
        if args.ms2lda_config is not None:
            ms2lda_config = json.loads(args.ms2lda_config.read_text(encoding="utf-8"))
            if not isinstance(ms2lda_config, dict):
                raise FitError(f"{args.ms2lda_config} does not hold a JSON object.")
        run_names = read_experiment_list(args.experiments_file) if args.experiments_file else None
        experiments = select_experiments(args.batch_dir, args.ionization_mode, run_names)

        mag = not args.no_mag
        if mag and args.ionization_mode == "neg":
            logger.info("MAG has a positive-mode Spec2Vec model only; fitting without MAG.")
            mag = False

        args.out.mkdir(parents=True, exist_ok=True)
        request_path = args.out / "request.json"
        request = build_request(
            experiments, args.out, args.ionization_mode, args.n_motifs, args.n_iterations,
            mag, args.viz, ms2lda_config,
        )
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        report = run_check(python, request_path)
    except FitError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    logger.info("MS2LDA environment: %s", ", ".join(
        f"{name} {version}" for name, version in report["versions"].items() if version
    ))
    logger.info("Fitting %d motifs on %d experiment(s)%s", args.n_motifs, len(experiments),
                "" if mag else " without MAG")
    exit_code = run_fit(python, request_path)
    if exit_code != 0:
        logger.error("The fit failed (exit code %d); see %s", exit_code, args.out / "fit.log")
        sys.exit(1)

    model = Mass2MotifModel.load(args.out)
    logger.info("Model %s written to %s", model.motif_set.model_id, args.out)


if __name__ == "__main__":
    main()
