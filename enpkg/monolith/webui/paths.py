"""Where the application keeps its working files.

Every path is relative to the process working directory, so the application must be
started from the repository root. Runs launched from here are always given an absolute
``--output-dir`` built from these, because the subprocess inherits that working directory
and would otherwise resolve its own relative default somewhere else.
"""
from __future__ import annotations

import secrets
from pathlib import Path

WORKSPACE_DIR = Path("gui_workspace")
DEFAULT_INPUT_DIR = WORKSPACE_DIR / "input"
DEFAULT_BATCH_DIR = WORKSPACE_DIR / "batch_input"
DATABASE_DIR = WORKSPACE_DIR / "databases"
DEFAULT_CONFIG_PATH = WORKSPACE_DIR / "gui_config.yaml"

# One directory per run, holding the config it was launched with, its logs, its Turtle
# export and its result artifact. Keeping them separate is what lets two runs proceed
# without overwriting each other's output.
RUNS_DIR = WORKSPACE_DIR / "runs"

_SECRET_PATH = WORKSPACE_DIR / ".gui_secret"


def ensure_workspace() -> None:
    """Create the directories the application writes into."""
    for directory in (DEFAULT_INPUT_DIR, DEFAULT_BATCH_DIR, DATABASE_DIR, RUNS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def storage_secret() -> str:
    """Return the key used to sign per-user storage cookies, creating it once.

    ``app.storage.user`` needs a stable secret: a value regenerated on each launch would
    invalidate every stored session, which is the whole reason that store is used instead
    of an in-memory dict. It is kept out of version control.
    """
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    if _SECRET_PATH.is_file():
        secret = _SECRET_PATH.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    secret = secrets.token_urlsafe(32)
    _SECRET_PATH.write_text(secret, encoding="utf-8")
    return secret
