"""``enpkg serialize`` — turn pickled analyses into RDF without re-running the pipeline."""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

import typer

from enpkg.cli._common import fail
from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.rdf import serialize_to_turtle


def serialize(
    analysis: list[Path] = typer.Argument(
        ..., help="One or more analysis.pkl files, as written by a batch run."
    ),
    out_dir: Path = typer.Option(
        Path("rdf_out"), "--out-dir", "-o", help="Where the .ttl files are written."
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Read serializer options from this config's `serializer` key."
    ),
    top_k_ms1: Optional[int] = typer.Option(None, help="Cap MS1 annotations per spectrum."),
    top_k_ms2: Optional[int] = typer.Option(None, help="Cap MS2 matches per spectrum."),
    top_k_sirius: Optional[int] = typer.Option(None, help="Cap SIRIUS candidates per spectrum."),
    no_fbmn_components: bool = typer.Option(
        False, "--no-fbmn-components", help="Skip the emi:FBMNComponent nodes."
    ),
    include_ions: bool = typer.Option(False, "--include-ions", help="Emit one node per peak."),
    min_relative_intensity: Optional[float] = typer.Option(
        None, help="Drop peaks below this fraction of the base peak."
    ),
    max_ions_per_spectrum: Optional[int] = typer.Option(
        None, help="Keep at most this many peaks per spectrum."
    ),
) -> None:
    """Serialize pickled analyses to Turtle.

    Options given on the command line override the ones read from ``--config``, so a
    saved configuration can be reused while varying one setting.
    """
    base = _config_from_file(config) if config else SerializerConfig()
    overrides = {
        "top_k_ms1": top_k_ms1,
        "top_k_ms2": top_k_ms2,
        "top_k_sirius": top_k_sirius,
        "min_relative_intensity": min_relative_intensity,
        "max_ions_per_spectrum": max_ions_per_spectrum,
    }
    settings = base.model_dump()
    settings.update({k: v for k, v in overrides.items() if v is not None})
    if no_fbmn_components:
        settings["include_fbmn_components"] = False
    if include_ions:
        settings["include_ions"] = True

    try:
        serializer_config = SerializerConfig.model_validate(settings)
    except Exception as exc:
        fail(f"Invalid serializer options:\n{exc}")

    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for pkl in analysis:
        if not pkl.is_file():
            typer.secho(f"[missing] {pkl}", fg=typer.colors.RED, err=True)
            failures += 1
            continue
        try:
            obj = pickle.loads(pkl.read_bytes())
            ttl_path = out_dir / f"{obj.run_name}.ttl"
            serialize_to_turtle(obj, str(ttl_path), **serializer_config.model_dump())
        except Exception as exc:
            typer.secho(f"[failed] {pkl}: {exc}", fg=typer.colors.RED, err=True)
            failures += 1
            continue
        typer.echo(f"  {obj.run_name} -> {ttl_path}")

    written = len(analysis) - failures
    typer.secho(
        f"Wrote {written} graph(s) to {out_dir}"
        + (f", {failures} failed" if failures else ""),
        fg=typer.colors.GREEN if not failures else typer.colors.YELLOW,
    )
    if failures:
        raise typer.Exit(code=1)


def _config_from_file(path: Path) -> SerializerConfig:
    """Read just the serializer section of a unified config."""
    if not path.is_file():
        fail(f"Config file not found: {path}")
    try:
        data = config_io.load_unified_yaml(path)
        return SerializerConfig.model_validate(config_io.get_serializer_section(data))
    except Exception as exc:
        fail(f"Could not read serializer options from {path}:\n{exc}")
