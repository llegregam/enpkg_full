"""Page modules.

Importing this package registers every ``@ui.page`` route, which is why the entry point
imports it rather than the individual modules.
"""
from enpkg.monolith.webui.pages import imports, pipeline, serializer  # noqa: F401
