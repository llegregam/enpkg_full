"""The frame every page is drawn inside: a header, navigation, and a run indicator.

The run indicator is why this is shared rather than repeated. A run started on the
pipeline page keeps going while the user is on another page, so every page has to be able
to show that something is in progress. It reads the session's run handle through a timer
the page owns, which is cancelled when that page is left.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from nicegui import ui

from enpkg.monolith.webui import runs, state

# The navigation bar, in order: (route, button label, Material icon name).
PAGES: list[tuple[str, str, str]] = [
    ("/imports", "Imports", "folder_open"),
    ("/pipeline", "Pipeline", "account_tree"),
    ("/serializer", "Serializer", "share"),
]

_STATUS_COLOURS = {
    "starting": "orange",
    "running": "orange",
    "cancelling": "orange",
    "cancelled": "grey",
    "failed": "red",
    "done": "green",
}


@contextmanager
def page_frame(active: str, title: str) -> Iterator[None]:
    """Draw the header and yield inside the page's main container."""
    ui.page_title(f"{title} — enpkg")

    with ui.header().classes("items-center justify-between px-4 py-2"):
        with ui.row().classes("items-center gap-1"):
            ui.label("enpkg").classes("text-lg font-bold mr-4")
            for route, label, icon in PAGES:
                button = ui.button(
                    label, icon=icon, on_click=lambda r=route: ui.navigate.to(r)
                ).props("flat no-caps")
                if route == active:
                    button.props("color=white").classes("font-bold underline")
                else:
                    button.props("color=grey-4")
        _run_indicator()

    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-4"):
        yield


def _run_indicator() -> None:
    """Show the session's current run, if any."""
    chip = ui.chip("", icon="bolt").props("outline color=white")
    chip.visible = False

    def refresh() -> None:
        handle = state.session().run or state.session().last_run
        if handle is None:
            chip.visible = False
            return
        chip.visible = True
        chip.text = runs.summary_line(handle)
        chip.props(f"outline color={_STATUS_COLOURS.get(handle.status, 'white')}")

    refresh()
    ui.timer(1.0, refresh)


def section(title: str, caption: str = "") -> ui.card:
    """Return a titled card to place content in."""
    card = ui.card().classes("w-full")
    with card:
        ui.label(title).classes("text-lg font-bold")
        if caption:
            ui.label(caption).classes("text-sm text-grey-7 -mt-2")
    return card
