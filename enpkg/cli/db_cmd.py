"""``enpkg db`` — populate the DuckDB database the annotation blocks read.

Both subcommands are pass-throughs: they hand their arguments to the import script that
owns them rather than redeclaring its flags, so the flag set is defined in one place and
``enpkg db lotus --help`` shows exactly what ``python -m enpkg.scripts.import_lotus
--help`` shows.
"""
from __future__ import annotations

import typer

from enpkg.scripts import import_lotus, import_spectral_library

db_app = typer.Typer(help="Build and inspect the reference database.", no_args_is_help=True)

# Turns off Typer's own parsing for these commands. `allow_extra_args` lets arguments it
# has no parameter for through instead of erroring, and `ignore_unknown_options` stops it
# claiming anything starting with a dash. Together they leave every argument after the
# subcommand name sitting in `ctx.args`, unparsed, ready to hand to argparse.
_PASSTHROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}


@db_app.command(
    "lotus",
    context_settings=_PASSTHROUGH,
    # Without this Typer would intercept --help and print its own (empty) argument list;
    # the string below is what `enpkg db --help` shows for this line, while --help placed
    # after `lotus` reaches argparse and prints the script's real arguments.
    add_help_option=False,
    help="Import the LOTUS compound tables. Run with --help for its arguments.",
)
def db_lotus(ctx: typer.Context) -> None:
    """Forward to the LOTUS importer.

    Bare ``enpkg db lotus`` becomes ``--help``: the importer's arguments are all
    required, so calling it with none would only produce an argparse usage error.
    """
    # `prog` is the name argparse prints in its usage line. Left alone it would show
    # `enpkg`, taken from sys.argv[0], and `enpkg --metadata ...` is not a command.
    import_lotus.main(ctx.args or ["--help"], prog="enpkg db lotus")


@db_app.command(
    "spectral-library",
    context_settings=_PASSTHROUGH,
    add_help_option=False,
    help="Register a FragHub spectral-library export. Run with --help for its arguments.",
)
def db_spectral_library(ctx: typer.Context) -> None:
    """Forward to the spectral-library importer.

    Bare ``enpkg db spectral-library`` becomes ``--help`` for the same reason as above.
    """
    import_spectral_library.main(ctx.args or ["--help"], prog="enpkg db spectral-library")
