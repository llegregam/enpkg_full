"""Choosing a directory, in a browser tab or in a native window.

The server cannot see the machine running the browser, so a served page has no access to
an operating-system file dialog. It gets a dialog listing the directories the *server*
can see, which in served mode is the machine holding the data. A native window is the
same process as the server, so pywebview's real folder dialog is available and is used
instead.

Both paths end at the same callback, so callers never branch on the mode.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from nicegui import app, run, ui


def is_native() -> bool:
    """Return whether the application is running in a native window."""
    return getattr(app, "native", None) is not None and app.native.main_window is not None


def list_subdirectories(path: Path) -> list[Path]:
    """Return the readable subdirectories of ``path``, sorted by name.

    A directory the process cannot read yields an empty list rather than raising, so an
    unreadable folder somewhere in the tree cannot stop the dialog from rendering.
    """
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except (OSError, PermissionError):
        return []


def roots() -> list[Path]:
    """Return the places a browse dialog can start from."""
    home = Path.home()
    if Path("/").exists() and not _is_windows():
        return [home, Path("/")]
    drives = [Path(f"{letter}:\\") for letter in "CDEFGH"]
    return [home, *[d for d in drives if d.exists()]]


def _is_windows() -> bool:
    import os

    return os.name == "nt"


def validate_directory(path: Path) -> Optional[str]:
    """Return an error describing why ``path`` is unusable, or None if it is fine."""
    expanded = path.expanduser()
    if not expanded.exists():
        return f"Does not exist: {expanded}"
    if not expanded.is_dir():
        return f"Not a directory: {expanded}"
    return None


class FolderPicker:
    """A path box with a Browse button, usable in both modes."""

    def __init__(
        self,
        label: str,
        value: str,
        on_pick: Callable[[Path], None],
    ) -> None:
        self._on_pick = on_pick

        with ui.row().classes("w-full items-start gap-2 no-wrap"):
            self.input = (
                ui.input(label=label, value=value, on_change=self._on_typed)
                .props("dense")
                .classes("grow")
            )
            ui.button(icon="folder_open", on_click=self._browse).props(
                "flat dense"
            ).tooltip("Browse for a folder")

        self.message = ui.label("").classes("text-xs -mt-2")
        self._validate(value)

    def _on_typed(self, _event=None) -> None:
        text = (self.input.value or "").strip()
        if self._validate(text):
            self._on_pick(Path(text).expanduser())

    def _validate(self, text: str) -> bool:
        """Update the caption and return whether the path is a usable directory."""
        if not text:
            self.message.text = "No folder chosen."
            self.message.classes(replace="text-xs text-grey-7")
            return False
        error = validate_directory(Path(text))
        if error:
            self.message.text = error
            self.message.classes(replace="text-xs text-red")
            return False
        self.message.text = str(Path(text).expanduser().resolve())
        self.message.classes(replace="text-xs text-grey-7")
        return True

    def set_value(self, path: Path) -> None:
        self.input.value = str(path)

    async def _browse(self) -> None:
        chosen = await _native_dialog(self.input.value) if is_native() else await _served_dialog(
            self.input.value
        )
        if chosen is not None:
            self.set_value(chosen)
            self._on_pick(chosen)


async def _native_dialog(current: str) -> Optional[Path]:
    """Open the operating system's folder dialog."""
    import webview

    start = Path(current).expanduser()
    directory = str(start if start.is_dir() else Path.home())
    result = await app.native.main_window.create_file_dialog(
        webview.FOLDER_DIALOG, directory=directory
    )
    if not result:
        return None
    return Path(result[0])


async def _served_dialog(current: str) -> Optional[Path]:
    """Open a dialog listing directories visible to the server."""
    start = Path(current).expanduser()
    if not start.is_dir():
        start = Path.home()

    chosen: dict[str, Optional[Path]] = {"path": None}
    cursor = {"path": start.resolve()}

    with ui.dialog() as dialog, ui.card().classes("w-[32rem]"):
        header = ui.label(str(cursor["path"])).classes("text-sm font-mono break-all")
        listing = ui.column().classes("w-full gap-0 max-h-80 overflow-auto")

        async def go(path: Path) -> None:
            cursor["path"] = path.resolve()
            header.text = str(cursor["path"])
            await render()

        async def render() -> None:
            listing.clear()
            # Listing a directory is blocking file access, and a network share can take
            # seconds; doing it on the event loop would freeze every other client.
            children = await run.io_bound(list_subdirectories, cursor["path"])
            with listing:
                parent = cursor["path"].parent
                if parent != cursor["path"]:
                    ui.button(
                        ".. (up one level)", icon="arrow_upward",
                        on_click=lambda p=parent: go(p),
                    ).props("flat dense no-caps align=left").classes("w-full")
                for child in children:
                    ui.button(
                        child.name, icon="folder",
                        on_click=lambda p=child: go(p),
                    ).props("flat dense no-caps align=left").classes("w-full")
                if not children:
                    ui.label("No subfolders.").classes("text-xs text-grey-7 p-2")

        with ui.row().classes("w-full justify-between items-center"):
            ui.label("Start from:").classes("text-xs text-grey-7")
            with ui.row().classes("gap-1"):
                for root in roots():
                    ui.button(
                        root.name or str(root), on_click=lambda p=root: go(p)
                    ).props("flat dense no-caps")

        await render()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def use_this() -> None:
                chosen["path"] = cursor["path"]
                dialog.close()

            ui.button("Use this folder", on_click=use_this).props("color=primary")

    await dialog
    return chosen["path"]
