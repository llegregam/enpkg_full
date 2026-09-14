"""Per-visitor state that survives moving between pages.

Navigating from one page to another is a full page load: the ``@ui.page`` function runs
again from scratch and every element object from the previous page is destroyed. Nothing
the user chose can therefore live in a page function's local variables. But module-level
variables in NiceGUI are shared by *every* connected client, so they cannot simply be
moved there either.

State is split by what it is made of:

``app.storage.user``
    Everything that survives ``json.dumps``: folder paths as strings, chosen filenames,
    the block selection, the raw form values. Per visitor, and it also survives a browser
    reload.

``SESSIONS``
    Everything that does not: running subprocesses, log buffers, parsed results. The
    dictionary is module-level, but no value is ever read from it except through the
    caller's own session id, so one visitor cannot see another's. With a single visitor —
    the native window — it degenerates to one entry and needs no special case.

Element handles and form objects belong to neither: they are rebuilt on every page load.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from nicegui import app

from enpkg.monolith.pipeline.blocks import BLOCKS
from enpkg.monolith.webui import paths

# One key inside app.storage.user, so nothing here collides with NiceGUI's own use of
# that store.
_ROOT = "enpkg"

# Sessions holding no run and untouched for this long are dropped when the registry is
# next consulted. Cleaning up on client disconnect would be wrong: navigating between
# pages *is* a disconnect, so it would discard a running pipeline on every page change.
_SESSION_TTL_SECONDS = 3600.0


@dataclass
class SessionObjects:
    """Per-visitor values that cannot be stored as JSON."""

    run: Any = None  # RunHandle for the run in progress, if any
    last_run: Any = None  # the most recently finished RunHandle
    touched_at: float = field(default_factory=time.monotonic)


SESSIONS: dict[str, SessionObjects] = {}


def _defaults() -> dict[str, Any]:
    return {
        "sid": uuid.uuid4().hex,
        "mode": "single",
        "input_dir": str(paths.DEFAULT_INPUT_DIR),
        "batch_dir": str(paths.DEFAULT_BATCH_DIR),
        "spectra": None,
        "metadata": None,
        "quant": None,
        "sirius_spectra": None,
        "config_path": str(paths.DEFAULT_CONFIG_PATH),
        "selected_blocks": [],
        "form_state": {},
        "general_params": {},
        "serializer": {},
        "verbose": False,
    }


def raw() -> dict[str, Any]:
    """Return this visitor's stored values, filling in any that are missing.

    Missing keys are added individually rather than by replacing the whole mapping.
    Replacing it would mean that a single access finding the mapping unfamiliar — which
    happens whenever the store differs from the one a value was written through — silently
    discards every choice the visitor has made.
    """
    store = app.storage.user
    current = store.get(_ROOT)
    if not isinstance(current, dict):
        current = {}
        store[_ROOT] = current
    for key, value in _defaults().items():
        current.setdefault(key, value)
    return current


def get(key: str, default: Any = None) -> Any:
    return raw().get(key, default)


def set_value(key: str, value: Any) -> None:
    """Store one value.

    Values must survive ``json.dumps``: a ``Path`` or a dataclass reaching this store
    fails when NiceGUI persists it, possibly long after the page rendered fine. Callers
    convert to ``str`` first.
    """
    raw()[key] = value


def update(values: dict[str, Any]) -> None:
    raw().update(values)


def sid() -> str:
    """Return this visitor's session id, the key into :data:`SESSIONS`."""
    return raw()["sid"]


def session() -> SessionObjects:
    """Return this visitor's non-JSON objects, creating the entry on first use."""
    _prune()
    key = sid()
    objects = SESSIONS.get(key)
    if objects is None:
        objects = SessionObjects()
        SESSIONS[key] = objects
    objects.touched_at = time.monotonic()
    return objects


def _prune() -> None:
    """Drop idle sessions that hold no run."""
    now = time.monotonic()
    for key, objects in list(SESSIONS.items()):
        if objects.run is None and now - objects.touched_at > _SESSION_TTL_SECONDS:
            del SESSIONS[key]


# --- typed accessors for the values pages read most -------------------------------


def _stored_path(key: str, fallback: Path) -> Path:
    """Return a stored path, falling back when it is missing or was cleared.

    An emptied box would otherwise become ``Path("")``, which is ``Path(".")`` — the
    working directory, silently and wrongly.
    """
    return Path(get(key) or str(fallback)).expanduser()


def input_path() -> Path:
    return _stored_path("input_dir", paths.DEFAULT_INPUT_DIR)


def batch_path() -> Path:
    return _stored_path("batch_dir", paths.DEFAULT_BATCH_DIR)


def config_path() -> Path:
    return _stored_path("config_path", paths.DEFAULT_CONFIG_PATH)


def selected_blocks() -> list[str]:
    """Return the selection in registry order, ignoring ids no longer registered."""
    chosen = set(get("selected_blocks") or [])
    return [block.id for block in BLOCKS if block.id in chosen]


def resolved_input(name: str) -> Optional[Path]:
    """Return the absolute path of a chosen input file, or None if none is chosen."""
    filename = get(name)
    return input_path() / filename if filename else None
