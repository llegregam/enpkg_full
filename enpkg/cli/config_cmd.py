"""``enpkg config`` — create, check and inspect a unified config YAML."""
from __future__ import annotations

from pathlib import Path

import typer
import yaml

from enpkg.cli._common import fail, load_run_config
from enpkg.monolith.configuration.introspect import (
    default_for,
    is_basemodel,
    unwrap_optional,
)
from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import (
    BLOCKS,
    BLOCKS_BY_ID,
    MS_SHARED_BLOCKS,
    MS_SHARED_KEY,
)

config_app = typer.Typer(help="Create and check config files.", no_args_is_help=True)


@config_app.command("init")
def config_init(
    out: Path = typer.Option(Path("enpkg_config.yaml"), "--out", "-o", help="Where to write."),
    blocks: str = typer.Option(
        "", "--blocks", "-b", help="Comma-separated block ids. Defaults to every block."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Write a config file holding every selected block's defaults."""
    if out.exists() and not force:
        fail(f"{out} already exists. Pass --force to overwrite it.")

    selected = [b.strip() for b in blocks.split(",") if b.strip()] or [b.id for b in BLOCKS]
    unknown = [b for b in selected if b not in BLOCKS_BY_ID]
    if unknown:
        fail(f"Unknown block ids: {', '.join(unknown)}")

    document: dict = {config_io.SELECTION_KEY: selected}
    needs_filling: list[str] = []
    seen_ms = False
    for block_id in selected:
        block = BLOCKS_BY_ID[block_id]
        if block.config_cls is None:
            continue
        if block_id in MS_SHARED_BLOCKS:
            if seen_ms:
                continue
            seen_ms = True
            key = MS_SHARED_KEY
        else:
            key = block_id
        document[key] = _template_section(block.config_cls, key, needs_filling)
    document[config_io.SERIALIZER_KEY] = SerializerConfig().model_dump()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    typer.secho(f"Wrote {out}", fg=typer.colors.GREEN)
    typer.echo(f"  blocks: {', '.join(selected)}")
    if needs_filling:
        typer.secho(
            "  fill these in before running — they have no default:",
            fg=typer.colors.YELLOW,
        )
        for path in needs_filling:
            typer.secho(f"    {path}", fg=typer.colors.YELLOW)


def _template_section(model_cls, prefix: str, needs_filling: list[str]) -> dict:
    """Return a section holding each field's default, or null where there is none.

    A required field is written as null and its path recorded, so the file is a complete
    list of what the block accepts rather than only the parts that happen to be
    optional. Such a file does not validate until those nulls are replaced, which is
    what ``enpkg config validate`` reports.
    """
    section: dict = {}
    for name, field_info in model_cls.model_fields.items():
        annotation, _ = unwrap_optional(field_info.annotation)
        path = f"{prefix}.{name}"
        if is_basemodel(annotation):
            section[name] = _template_section(annotation, path, needs_filling)
        elif field_info.is_required():
            section[name] = None
            needs_filling.append(path)
        else:
            section[name] = default_for(field_info)
    return section


@config_app.command("validate")
def config_validate(
    path: Path = typer.Argument(..., help="Config YAML to check."),
) -> None:
    """Check that a config file describes a runnable pipeline."""
    selected, configs, serializer_config = load_run_config(path)
    typer.secho(f"{path} is valid.", fg=typer.colors.GREEN)
    typer.echo(f"  blocks: {', '.join(selected)}")

    missing_deps = [
        f"{block_id} needs {dep}"
        for block_id in selected
        for dep in BLOCKS_BY_ID[block_id].depends_on
        if dep not in selected
    ]
    if missing_deps:
        typer.secho(
            "  warning: unmet block dependencies, these will be skipped at run time:",
            fg=typer.colors.YELLOW,
        )
        for line in missing_deps:
            typer.secho(f"    {line}", fg=typer.colors.YELLOW)


@config_app.command("show")
def config_show(
    path: Path = typer.Argument(..., help="Config YAML to print."),
) -> None:
    """Print a config as it will be interpreted, after validation and defaults."""
    selected, configs, serializer_config = load_run_config(path)
    resolved = {
        config_io.SELECTION_KEY: selected,
        **{bid: cfg.model_dump() for bid, cfg in configs.items() if cfg is not None},
        config_io.SERIALIZER_KEY: serializer_config.model_dump(),
    }
    typer.echo(yaml.safe_dump(resolved, sort_keys=False, default_flow_style=False))
