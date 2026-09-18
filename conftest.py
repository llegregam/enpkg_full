"""Root pytest configuration.

Exists to register the NiceGUI test plugin. pytest 8 only honours ``pytest_plugins`` in
the rootdir's conftest, and the plugin is registered conditionally so the suite still
collects when the optional ``webui`` dependency group is not installed — the tests that
need it skip themselves.

Only ``user_plugin`` is loaded, not ``nicegui.testing.plugin``: the latter also pulls in
the ``screen`` fixtures, which require Selenium and a real browser. Nothing here needs
one.
"""

pytest_plugins: list[str] = []

try:  # pragma: no cover - depends on which dependency groups are installed
    import nicegui  # noqa: F401
except ImportError:
    pass
else:
    pytest_plugins.append("nicegui.testing.user_plugin")
