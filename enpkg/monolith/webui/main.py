"""Starting the NiceGUI application.

Importing :mod:`enpkg.monolith.webui.pages` is what registers the routes; nothing else
refers to those modules.
"""
from __future__ import annotations

from nicegui import app, ui

from enpkg.monolith.webui import paths, runs


def run_app(
    *,
    native: bool = False,
    host: str = "127.0.0.1",
    port: int = 8080,
    show: bool = True,
    window_size: tuple[int, int] = (1400, 900),
) -> None:
    """Start the server and block until it stops.

    ``host`` defaults to the loopback address: a served page offers a dialog that browses
    the *server's* filesystem, so binding to every interface exposes that to anyone who
    can reach the port. Serving to a network is a deliberate act, not a default.
    """
    paths.ensure_workspace()

    from enpkg.monolith.webui import pages  # noqa: F401  (registers the routes)

    app.on_shutdown(runs.shutdown_all)

    ui.run(
        host=host,
        port=port,
        title="enpkg",
        favicon="🧪",
        storage_secret=paths.storage_secret(),
        native=native,
        window_size=window_size if native else None,
        show=show and not native,
        # Auto-reload re-imports modules, which would discard the registry holding every
        # running subprocess: the run would carry on with nothing reading its output and
        # no way to stop it.
        reload=False,
    )
