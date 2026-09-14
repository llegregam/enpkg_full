"""Shared setup for the NiceGUI front-end tests.

The plugin providing the ``user`` fixture is registered in the root conftest, which is
the only place pytest 8 honours ``pytest_plugins``.
"""
import shutil

import pytest

pytest.importorskip(
    "nicegui",
    reason="the webui dependency group is not installed (poetry install --with webui)",
)

from nicegui import app  # noqa: E402
from nicegui.storage import Storage  # noqa: E402


@pytest.fixture(autouse=True)
def _tolerate_storage_cleanup(monkeypatch):
    """Stop a NiceGUI cleanup failure on Windows from cascading through the suite.

    ``Storage.clear`` unlinks the storage files and then calls ``rmdir`` on the
    directory. Windows refuses to unlink a file the writing task still holds open, which
    is the case for the ``.json.tmp`` of an atomic write still in flight when a test ends
    — and any page writing to ``app.storage.user`` can leave one. NiceGUI suppresses that
    unlink error by design, so the file survives and the unconditional ``rmdir`` then
    raises "directory not empty". The failure lands in the ``user`` fixture's teardown,
    which leaves NiceGUI half-reset, so every later test gets 404s for routes that are
    registered correctly.

    Removing the tree is equivalent for a temporary directory and confines the damage.
    Written up in ``docs/NICEGUI_STORAGE_CLEANUP_BUG.md``.

    Autouse and depending on nothing, so it is set up before ``user`` and torn down after
    it — the patch is still in place when the failure would otherwise happen.
    """
    original = Storage.clear

    def clear(self) -> None:
        try:
            original(self)
        except OSError:
            shutil.rmtree(self.path, ignore_errors=True)

    monkeypatch.setattr(Storage, "clear", clear)
    yield


@pytest.fixture(autouse=True)
def _storage_secret():
    """Give ``app.storage.user`` the signing key ``ui.run`` would normally supply.

    Without it every page raises on first access, because per-visitor storage refuses to
    work unsigned.
    """
    previous = Storage.secret
    Storage.secret = "test-secret"
    yield
    Storage.secret = previous


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """Point the application's workspace at a temporary directory."""
    from enpkg.monolith.webui import paths

    monkeypatch.setattr(paths, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(paths, "DEFAULT_INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(paths, "DEFAULT_BATCH_DIR", tmp_path / "batch_input")
    monkeypatch.setattr(paths, "DATABASE_DIR", tmp_path / "databases")
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    paths.ensure_workspace()
    yield


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Drop the per-visitor objects registry between tests."""
    from enpkg.monolith.webui.state import SESSIONS

    SESSIONS.clear()
    yield
    SESSIONS.clear()


_ = app
