"""``enpkg run`` and ``enpkg batch`` — execute the pipeline."""
from __future__ import annotations

import queue
from pathlib import Path
from typing import Optional

import typer

from enpkg.cli._common import (
    duration_rows,
    fail,
    load_run_config,
    resolve_ionization_mode,
)
from enpkg.monolith.pipeline.batch_runner import (
    METADATA_SUFFIXES,
    QUANT_SUFFIXES,
    SPECTRA_SUFFIXES,
    discover_experiments,
    find_shared_metadata,
    run_batch,
)
from enpkg.monolith.pipeline.run_artifact import (
    build_batch_artifact,
    build_run_artifact,
    write_artifact,
)
from enpkg.monolith.pipeline.runner import run_pipeline

batch_app = typer.Typer(help="Run the pipeline over many experiments.", no_args_is_help=True)


def _first_match(folder: Path, suffixes: tuple[str, ...]) -> Optional[Path]:
    """Return the first file in ``folder`` matching ``suffixes``, earlier ones winning.

    ``suffixes`` is a preference order, not a set: metadata is ``(.tsv, .txt, .csv)``
    and quant tables are ``.csv``, so a folder holding both a ``.txt`` metadata file and
    a ``.csv`` quant table must resolve metadata to the ``.txt``. Scanning files first
    and accepting any matching suffix would resolve it to whichever sorts earlier, which
    for a typical folder is the quant table.
    """
    if not folder.is_dir():
        return None
    for suffix in suffixes:
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() == suffix:
                return path
    return None


def _resolve_inputs(
    input_dir: Optional[Path],
    spectra: Optional[Path],
    metadata: Optional[Path],
    quant: Optional[Path],
) -> tuple[Path, Path, Path]:
    """Resolve the three input files, filling any that were not named from a folder.

    Naming a file always wins over discovery, so an explicit path is never silently
    replaced by a same-suffix neighbour.
    """
    if input_dir is not None:
        spectra = spectra or _first_match(input_dir, SPECTRA_SUFFIXES)
        metadata = metadata or _first_match(input_dir, METADATA_SUFFIXES)
        quant = quant or _first_match(input_dir, QUANT_SUFFIXES)

    missing = [
        name
        for name, value in (("spectra", spectra), ("metadata", metadata), ("quant", quant))
        if value is None
    ]
    if missing:
        fail(
            f"No {', '.join(missing)} file given. Pass --{missing[0]}, or --input-dir "
            "with a folder holding one of each."
        )
    for name, value in (("spectra", spectra), ("metadata", metadata), ("quant", quant)):
        if not value.is_file():
            fail(f"{name} file not found: {value}")
    return spectra, metadata, quant


def _report_run(result, verbose: bool) -> None:
    """Print a run's outcome, its per-stage timings and where its outputs landed."""
    if result.error:
        typer.secho(f"Failed: {result.error}", fg=typer.colors.RED, err=True)
    else:
        typer.secho(
            f"Finished. Executed: {', '.join(result.executed) or 'nothing'}",
            fg=typer.colors.GREEN,
        )
    if result.skipped:
        typer.secho(f"Skipped: {', '.join(result.skipped)}", fg=typer.colors.YELLOW)

    summary = result.summary
    if summary is None and result.analysis is not None:
        from enpkg.monolith.pipeline.runner import AnalysisSummary

        summary = AnalysisSummary.from_analysis(result.analysis)
    if summary is not None:
        typer.echo(
            f"  spectra={summary.n_spectra}  ott_matches={summary.n_ott_matches}  "
            f"network_nodes={summary.n_network_nodes}"
        )

    if verbose and result.durations:
        typer.echo("Timings:")
        for label, seconds in duration_rows(result.durations):
            typer.echo(f"  {label:<24} {seconds:8.1f}s")

    for label, path in (
        ("log", result.log_file),
        ("summary", result.summary_file),
        ("graph", result.ttl_file),
    ):
        if path is not None:
            typer.echo(f"  {label}: {path}")


def run(
    config: Path = typer.Option(..., "--config", "-c", help="Unified config YAML."),
    input_dir: Optional[Path] = typer.Option(
        None, "--input-dir", "-i", help="Folder to discover spectra/metadata/quant in."
    ),
    spectra: Optional[Path] = typer.Option(None, help="Spectra file (.mgf/.mzml/.mzxml)."),
    metadata: Optional[Path] = typer.Option(None, help="Sample metadata file."),
    quant: Optional[Path] = typer.Option(None, help="Quantification table."),
    sirius_spectra: Optional[Path] = typer.Option(
        None, help="Spectra file for SIRIUS. Required when the sirius block is selected."
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", "-o", help="Where logs and the Turtle export are written."
    ),
    json_out: Optional[Path] = typer.Option(
        None, "--json-out", help="Write a machine-readable result artifact here."
    ),
    ionization_mode: Optional[str] = typer.Option(
        None, help="Override the config's ionization mode. Must not contradict it."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Run the pipeline against one experiment."""
    selected, configs, serializer_config = load_run_config(config)
    spectra, metadata, quant = _resolve_inputs(input_dir, spectra, metadata, quant)
    mode = resolve_ionization_mode(configs, ionization_mode)

    if "sirius" in selected:
        if sirius_spectra is None:
            sirius_spectra = _sirius_sibling(spectra)
        if sirius_spectra is None or not sirius_spectra.is_file():
            fail(
                "The sirius block is selected but no SIRIUS spectra file was found. "
                f"Pass --sirius-spectra, or place {spectra.stem}_sirius{spectra.suffix} "
                f"next to the spectra file."
            )
        configs["sirius"].sirius_params.path_to_input_spectra = str(sirius_spectra)

    result = run_pipeline(
        selected_ids=list(selected),
        configs=configs,
        spectra_path=spectra,
        metadata_path=metadata,
        quant_path=quant,
        ionization_mode=mode,
        log_queue=queue.Queue(),
        verbose=verbose,
        output_dir=output_dir,
        serializer_config=serializer_config,
    )

    _report_run(result, verbose)
    if json_out is not None:
        write_artifact(build_run_artifact(result), json_out)
        typer.echo(f"  result: {json_out}")
    if result.error:
        raise typer.Exit(code=1)


def _sirius_sibling(spectra: Path) -> Optional[Path]:
    """Return the ``<stem>_sirius<suffix>`` neighbour of ``spectra`` if it exists."""
    candidate = spectra.with_name(f"{spectra.stem}_sirius{spectra.suffix}")
    return candidate if candidate.is_file() else None


@batch_app.callback(invoke_without_command=True)
def batch(
    ctx: typer.Context,
    config: Path = typer.Option(None, "--config", "-c", help="Unified config YAML."),
    parent_dir: Optional[Path] = typer.Option(
        None, "--parent-dir", "-p", help="Folder holding shared metadata + one subfolder per experiment."
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", "-o", help="Where the batch folder is created."
    ),
    json_out: Optional[Path] = typer.Option(
        None, "--json-out", help="Write a machine-readable result artifact here."
    ),
    ionization_mode: Optional[str] = typer.Option(
        None, help="Override the config's ionization mode. Must not contradict it."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Run the pipeline against every experiment under a parent folder."""
    if ctx.invoked_subcommand is not None:
        return
    if config is None or parent_dir is None:
        fail("Both --config and --parent-dir are required.")
    if not parent_dir.is_dir():
        fail(f"Not a directory: {parent_dir}")

    selected, configs, serializer_config = load_run_config(config)
    mode = resolve_ionization_mode(configs, ionization_mode)

    result = run_batch(
        parent_dir=parent_dir,
        selected_ids=list(selected),
        configs=configs,
        ionization_mode=mode,
        log_queue=queue.Queue(),
        verbose=verbose,
        output_dir=output_dir,
        serializer_config=serializer_config,
    )

    if result.error:
        typer.secho(f"Batch failed: {result.error}", fg=typer.colors.RED, err=True)
    else:
        typer.secho(
            f"Batch finished: {len(result.succeeded)} ok, {len(result.failed)} failed",
            fg=typer.colors.GREEN if not result.failed else typer.colors.YELLOW,
        )
        for run_result in result.results:
            name = run_result.summary.run_name if run_result.summary else "(unknown)"
            mark = "FAIL" if run_result.error else "ok"
            typer.echo(f"  [{mark}] {name}")
    if result.batch_dir:
        typer.echo(f"  batch folder: {result.batch_dir}")

    if json_out is not None:
        write_artifact(build_batch_artifact(result), json_out)
        typer.echo(f"  result: {json_out}")
    if result.error or result.failed:
        raise typer.Exit(code=1)


@batch_app.command("discover")
def batch_discover(
    parent_dir: Path = typer.Option(..., "--parent-dir", "-p", help="Batch parent folder."),
) -> None:
    """List the experiments a batch run would pick up, without running anything."""
    if not parent_dir.is_dir():
        fail(f"Not a directory: {parent_dir}")

    metadata = find_shared_metadata(parent_dir)
    experiments = discover_experiments(parent_dir)

    if metadata is None:
        typer.secho(
            f"No shared metadata file at {parent_dir} "
            f"(looked for {', '.join(METADATA_SUFFIXES)}).",
            fg=typer.colors.RED,
        )
    else:
        typer.echo(f"metadata: {metadata.name}")

    if not experiments:
        typer.secho(
            "No experiment subfolders with both a spectra and a quant file.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    typer.echo(f"{len(experiments)} experiment(s):")
    for exp in experiments:
        sirius = " +sirius" if exp.sirius_spectra_path else ""
        typer.echo(f"  {exp.run_name:<28} {exp.subfolder.name}/{sirius}")
