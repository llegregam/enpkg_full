"""Makes ``python -m enpkg.cli`` equivalent to the ``enpkg`` console script.

The GUI spawns the pipeline through this form rather than the console script, so it
inherits the exact interpreter and virtual environment it is itself running in without
depending on anything being on PATH.
"""
from enpkg.cli.main import app

if __name__ == "__main__":
    app()
