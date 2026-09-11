"""Skip the NiceGUI front-end tests when its optional dependency group is absent.

The plugin providing the ``user`` fixture is registered in the root conftest, which is
the only place pytest 8 honours ``pytest_plugins``.
"""
import pytest

pytest.importorskip(
    "nicegui",
    reason="the webui dependency group is not installed (poetry install --with webui)",
)
