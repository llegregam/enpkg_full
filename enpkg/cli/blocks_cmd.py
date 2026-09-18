"""``enpkg blocks`` — inspect the pipeline block registry."""
from __future__ import annotations

import typer

from enpkg.monolith.pipeline.blocks import BLOCKS

blocks_app = typer.Typer(help="Inspect the pipeline blocks.", no_args_is_help=True)


@blocks_app.command("list")
def blocks_list(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show descriptions."),
) -> None:
    """List the pipeline blocks in the order a run executes them."""
    typer.echo(f"{len(BLOCKS)} block(s), in execution order:\n")
    for block in BLOCKS:
        flags = []
        if block.config_cls is None:
            flags.append("no config")
        if block.depends_on:
            flags.append(f"needs {', '.join(block.depends_on)}")
        if block.requires:
            flags.append(f"uses {', '.join(sorted(block.requires))}")
        suffix = f"  [{'; '.join(flags)}]" if flags else ""
        typer.echo(f"  {block.id:<14} {block.label}{suffix}")
        if verbose and block.description:
            typer.echo(f"                 {block.description}")
