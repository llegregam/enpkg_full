"""Entry script the NiceGUI test harness loads to build the application under test.

The harness clears the route table before each test and then imports this file to
rebuild it, so every test starts from freshly registered pages. It expects an entry
script in the shape NiceGUI applications normally take — imports that register pages,
then a ``ui.run()`` call, which the harness intercepts rather than executing.

This exists so the real entry point does not have to take that shape.
:func:`enpkg.monolith.webui.main.run_app` is a function, called with arguments parsed
from the command line, so importing it starts nothing.

The filename matters: pytest collects ``test_*.py`` and ``*_test.py``, and a name
matching either would be imported as a test module, running the ``ui.run()`` below for
real and blocking collection forever.
"""
from nicegui import ui

from enpkg.monolith.webui import pages  # noqa: F401  (registers the routes)

ui.run(storage_secret="test-secret")
