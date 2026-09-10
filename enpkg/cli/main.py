"""Entry point for the ``enpkg`` command line.

``app`` is what the ``enpkg`` console script and ``python -m enpkg.cli`` both call.
Subcommands live one per module and are attached here.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import typer

from enpkg.cli.blocks_cmd import blocks_app
from enpkg.cli.config_cmd import config_app
from enpkg.cli.db_cmd import db_app
from enpkg.cli.run import batch_app, run
from enpkg.cli.serialize_cmd import serialize

app = typer.Typer(
    help="Build a knowledge graph from an LC-MS/MS metabolomics dataset.",
    no_args_is_help=True,
    add_completion=False,
)

# `run` and `serialize` are plain functions rather than pre-decorated commands, so that
# the modules defining them stay importable (and testable) without building a Typer app.
app.command("run")(run)
app.command("serialize")(serialize)

app.add_typer(batch_app, name="batch")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(blocks_app, name="blocks")


def _version_callback(value: bool) -> None:
    if not value:
        return
    try:
        typer.echo(version("enpkg"))
    except PackageNotFoundError:
        typer.echo("unknown (enpkg is not installed as a distribution)")
    raise typer.Exit()


@app.callback()
def _main(
    version_flag: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Print the installed version and exit.",
    ),
) -> None:
    pass


if __name__ == "__main__":
    app()
