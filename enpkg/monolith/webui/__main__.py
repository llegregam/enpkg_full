"""``python -m enpkg.monolith.webui`` — start the front end.

``enpkg gui`` launches this as a new process rather than calling :func:`run_app` itself.
Native mode starts pywebview in a second process, and on Windows that process re-imports
the entry module; reaching ``ui.run`` during that import would start a second server.
The ``__main__`` guard below is what prevents it.
"""
from __future__ import annotations

import argparse

from enpkg.monolith.webui.main import run_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m enpkg.monolith.webui",
        description="Start the enpkg graphical interface.",
    )
    parser.add_argument("--native", action="store_true", help="Open a desktop window.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--no-show", action="store_true", help="Do not open a browser automatically."
    )
    args = parser.parse_args(argv)

    run_app(
        native=args.native,
        host=args.host,
        port=args.port,
        show=not args.no_show,
    )


if __name__ == "__main__":
    main()
