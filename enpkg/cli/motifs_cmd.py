"""``enpkg motifs`` — fit the MS2LDA model the ``mass2motif`` block reads.

``fit`` is a pass-through: it hands its arguments to
:mod:`enpkg.scripts.fit_mass2motifs`, so ``enpkg motifs fit --help`` shows that
script's own argparse help.
"""
from __future__ import annotations

import typer

from enpkg.scripts import fit_mass2motifs

motifs_app = typer.Typer(help="Fit Mass2Motif models (MS2LDA 2.0).", no_args_is_help=True)

# Leaves every argument after the subcommand name unparsed in `ctx.args`, as `enpkg db` does.
_PASSTHROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}


@motifs_app.command(
    "fit",
    context_settings=_PASSTHROUGH,
    add_help_option=False,
    help="Fit an MS2LDA 2.0 model on a batch folder. Run with --help for its arguments.",
)
def motifs_fit(ctx: typer.Context) -> None:
    """Forward to the fit script; bare ``enpkg motifs fit`` prints its help."""
    fit_mass2motifs.main(ctx.args or ["--help"], prog="enpkg motifs fit")
