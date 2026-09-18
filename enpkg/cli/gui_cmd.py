"""``enpkg gui`` — start the graphical interface."""
from __future__ import annotations

import subprocess
import sys

import typer

from enpkg.cli._common import fail


def gui(
    native: bool = typer.Option(
        False, "--native", help="Open a desktop window instead of a browser tab."
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Address to serve on. The default reaches this machine only.",
    ),
    port: int = typer.Option(8080, "--port", "-p", help="Port to serve on."),
    no_show: bool = typer.Option(
        False, "--no-show", help="Do not open a browser automatically."
    ),
) -> None:
    """Start the graphical interface.

    Serving on an address other than the default exposes a dialog that browses this
    machine's filesystem to anyone who can reach the port.
    """
    try:
        import nicegui  # noqa: F401
    except ImportError:
        fail(
            "The graphical interface needs the optional `webui` dependency group:\n"
            "    poetry install --with webui"
        )

    argv = [sys.executable, "-m", "enpkg.monolith.webui"]
    if native:
        argv.append("--native")
    else:
        argv += ["--host", host, "--port", str(port)]
        if no_show:
            argv.append("--no-show")

    # Launched as its own process: in native mode pywebview starts a second process that
    # re-imports the entry module, and running that inside this one would start a second
    # server.
    raise typer.Exit(code=subprocess.call(argv))
