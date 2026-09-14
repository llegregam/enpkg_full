"""Choosing a folder or a file, in a browser tab or in a native window.

The server cannot see the machine running the browser, so a served page has no access to
an operating-system file dialog. It gets a dialog listing what the *server* can see,
which in served mode is the machine holding the data. A native window is the same process
as the server, so pywebview's real dialogs are available and are used instead.

Both routes end at the same callback, so callers never branch on the mode.

Three kinds of choice are supported, differing in what the dialog offers and in what
counts as a usable value: an existing directory, an existing file, and a file to write
that need not exist yet.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Iterable, Optional

from nicegui import app, run, ui

log = logging.getLogger(__name__)

# What the picker is for. The value decides which native dialog is opened, what the
# served dialog lists, and which paths validate.
MODE_FOLDER = "folder"
MODE_OPEN = "open"
MODE_SAVE = "save"

CONFIG_SUFFIXES = (".yaml", ".yml")


def is_native() -> bool:
    """Return whether the application is running in a native window."""
    return getattr(app, "native", None) is not None and app.native.main_window is not None


def _is_windows() -> bool:
    return os.name == "nt"


def list_subdirectories(path: Path) -> list[Path]:
    """Return the readable subdirectories of ``path``, sorted by name.

    A directory the process cannot read yields an empty list rather than raising, so an
    unreadable folder somewhere in the tree cannot stop the dialog from rendering.
    """
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except (OSError, PermissionError):
        return []


def list_files(path: Path, suffixes: Iterable[str] = ()) -> list[Path]:
    """Return the readable files in ``path``, optionally filtered by suffix."""
    wanted = tuple(s.lower() for s in suffixes)
    try:
        entries = sorted(p for p in path.iterdir() if p.is_file())
    except (OSError, PermissionError):
        return []
    if not wanted:
        return entries
    return [p for p in entries if p.suffix.lower() in wanted]


def roots() -> list[Path]:
    """Return the places a browse dialog can start from."""
    home = Path.home()
    if not _is_windows():
        return [home, Path("/")]
    drives = [Path(f"{letter}:\\") for letter in "CDEFGH"]
    return [home, *[d for d in drives if d.exists()]]


def validate_directory(path: Path) -> Optional[str]:
    """Return an error describing why ``path`` is not a usable directory, else None."""
    expanded = path.expanduser()
    if not expanded.exists():
        return f"Does not exist: {expanded}"
    if not expanded.is_dir():
        return f"Not a directory: {expanded}"
    return None


def validate_existing_file(path: Path) -> Optional[str]:
    """Return an error describing why ``path`` is not a usable file, else None."""
    expanded = path.expanduser()
    if not expanded.exists():
        return f"Does not exist: {expanded}"
    if not expanded.is_file():
        return f"Not a file: {expanded}"
    return None


def validate_writable_file(path: Path) -> Optional[str]:
    """Return an error describing why ``path`` cannot be written, else None.

    The file itself need not exist — this is for a path that is about to be created —
    but the directory holding it must, since nothing here creates directories.
    """
    expanded = path.expanduser()
    if expanded.is_dir():
        return f"Is a directory: {expanded}"
    parent = expanded.parent
    if not parent.exists():
        return f"Folder does not exist: {parent}"
    return None


def dialog_start_directory(current: str) -> str:
    """Return an absolute directory for a dialog to open at.

    The path must be absolute. A dialog runs in the operating system's shell, which has
    no notion of the working directory this process was started from, and handing the
    native dialog a relative path makes it hang rather than fail — the button then looks
    as though it does nothing. Stored values are whatever the user typed, and the
    defaults are relative, so resolving here is what keeps that from happening.

    A value naming a file resolves to the folder holding it. Anything unusable falls back
    to the home directory.
    """
    # Checked before constructing a Path: Path("") is Path("."), which is a directory, so
    # an empty box would otherwise open at the working directory.
    if (current or "").strip():
        try:
            candidate = Path(current).expanduser()
            if candidate.is_dir():
                return str(candidate.resolve())
            parent = candidate.parent
            if parent.is_dir():
                return str(parent.resolve())
        except OSError:
            pass
    return str(Path.home().resolve())


class PathPicker:
    """A path box with a Browse button, usable in both modes."""

    def __init__(
        self,
        label: str,
        value: str,
        on_pick: Callable[[Path], None],
        *,
        mode: str = MODE_FOLDER,
        suffixes: tuple[str, ...] = (),
        file_type_label: str = "Files",
        marker: str = "",
    ) -> None:
        self.mode = mode
        self.suffixes = suffixes
        self.file_type_label = file_type_label
        self._on_pick = on_pick

        with ui.row().classes("w-full items-start gap-2 no-wrap"):
            self.input = (
                ui.input(label=label, value=value, on_change=self._on_typed)
                .props("dense")
                .classes("grow")
            )
            if marker:
                self.input.mark(marker)
            ui.button(icon="folder_open", on_click=self._browse).props(
                "flat dense"
            ).tooltip("Browse")

        self.message = ui.label("").classes("text-xs -mt-2")
        self._validate(value)

    # --- the path box -------------------------------------------------------------

    def _on_typed(self, _event=None) -> None:
        """Store what was typed, whether or not it is usable yet.

        Validation is advisory: it colours the caption. Storing only valid paths would
        leave the box showing one thing and the stored value being another, so an action
        taken afterwards would quietly use the previous path instead of reporting that
        this one is unusable.
        """
        text = (self.input.value or "").strip()
        self._validate(text)
        if text:
            self._on_pick(Path(text).expanduser())

    def _validate(self, text: str) -> bool:
        """Update the caption and return whether the path is usable for this mode."""
        if not text:
            self.message.text = "Nothing chosen."
            self.message.classes(replace="text-xs text-grey-7")
            return False
        error = self._error_for(Path(text))
        if error:
            self.message.text = error
            self.message.classes(replace="text-xs text-red")
            return False
        self.message.text = str(Path(text).expanduser().resolve())
        self.message.classes(replace="text-xs text-grey-7")
        return True

    def _error_for(self, path: Path) -> Optional[str]:
        if self.mode == MODE_FOLDER:
            return validate_directory(path)
        if self.mode == MODE_SAVE:
            return validate_writable_file(path)
        return validate_existing_file(path)

    def set_value(self, path: Path) -> None:
        self.input.value = str(path)

    # --- browsing -----------------------------------------------------------------

    async def _browse(self) -> None:
        try:
            if is_native():
                chosen = await self._native_dialog()
            else:
                chosen = await self._served_dialog()
        except Exception as exc:  # noqa: BLE001
            # Without this the button appears to do nothing at all, which gives no
            # indication that anything went wrong or where to look.
            log.exception("Path dialog failed")
            ui.notify(f"Could not open the chooser: {exc}", type="negative")
            return
        if chosen is not None:
            self.set_value(chosen)
            self._on_pick(chosen)

    async def _native_dialog(self) -> Optional[Path]:
        """Open the operating system's dialog for this mode."""
        import webview

        window = app.native.main_window
        start = dialog_start_directory(self.input.value)

        if self.mode == MODE_FOLDER:
            result = await window.create_file_dialog(
                webview.FileDialog.FOLDER, directory=start
            )
        elif self.mode == MODE_SAVE:
            result = await window.create_file_dialog(
                webview.FileDialog.SAVE,
                directory=start,
                save_filename=Path(self.input.value or "config.yaml").name,
                file_types=self._native_file_types(),
            )
        else:
            result = await window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=start,
                allow_multiple=False,
                file_types=self._native_file_types(),
            )
        return _first_path(result)

    def _native_file_types(self) -> tuple[str, ...]:
        return native_file_types(self.suffixes, self.file_type_label)

    async def _served_dialog(self) -> Optional[Path]:
        return await served_browser(
            self.input.value,
            mode=self.mode,
            suffixes=self.suffixes,
        )


def native_file_types(suffixes: tuple[str, ...], label: str) -> tuple[str, ...]:
    """Return pywebview's filter strings: a description with its patterns in brackets."""
    if not suffixes:
        return ()
    patterns = ";".join(f"*{s}" for s in suffixes)
    return (f"{label} ({patterns})", "All files (*.*)")


async def choose_save_target(
    current: str,
    *,
    suffixes: tuple[str, ...] = CONFIG_SUFFIXES,
    file_type_label: str = "YAML files",
    default_name: str = "config.yaml",
) -> Optional[Path]:
    """Ask where to write a file, and return the path or None if cancelled.

    The file need not exist. Kept here rather than on a page so that a caller wanting a
    save target does not have to know which of the two dialogs it will get.
    """
    if is_native():
        import webview

        result = await app.native.main_window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=dialog_start_directory(current),
            save_filename=Path(current).name or default_name,
            file_types=native_file_types(suffixes, file_type_label),
        )
        return _first_path(result)
    return await served_browser(current, mode=MODE_SAVE, suffixes=suffixes)


def _first_path(result) -> Optional[Path]:
    """Normalise what pywebview returns into a single path, or None if cancelled.

    An open or folder dialog returns a sequence; a save dialog returns a bare string.
    """
    if not result:
        return None
    if isinstance(result, (str, os.PathLike)):
        return Path(result)
    return Path(result[0])


async def served_browser(
    current: str,
    *,
    mode: str = MODE_FOLDER,
    suffixes: tuple[str, ...] = (),
) -> Optional[Path]:
    """Browse what the server can see and return the chosen path, or None if cancelled."""
    chosen: dict[str, Optional[Path]] = {"path": None}
    cursor = {"path": Path(dialog_start_directory(current))}
    typed_name = Path(current).name if mode == MODE_SAVE and current else ""

    with ui.dialog() as dialog, ui.card().classes("w-[34rem]"):
        header = ui.label(str(cursor["path"])).classes("text-sm font-mono break-all")
        listing = ui.column().classes("w-full gap-0 max-h-80 overflow-auto")

        name_input = None
        if mode == MODE_SAVE:
            name_input = (
                ui.input("File name", value=typed_name or "config.yaml")
                .props("dense")
                .classes("w-full")
            )

        def finish(path: Optional[Path]) -> None:
            chosen["path"] = path
            dialog.close()

        async def go(path: Path) -> None:
            cursor["path"] = path.resolve()
            header.text = str(cursor["path"])
            await render()

        async def render() -> None:
            listing.clear()
            # Listing a directory is blocking file access, and a network share can take
            # seconds; doing it on the event loop would stall every other client.
            children = await run.io_bound(list_subdirectories, cursor["path"])
            files = (
                await run.io_bound(list_files, cursor["path"], suffixes)
                if mode != MODE_FOLDER
                else []
            )
            with listing:
                parent = cursor["path"].parent
                if parent != cursor["path"]:
                    ui.button(
                        ".. (up one level)", icon="arrow_upward",
                        on_click=lambda p=parent: go(p),
                    ).props("flat dense no-caps align=left").classes("w-full")
                for child in children:
                    ui.button(
                        child.name, icon="folder", on_click=lambda p=child: go(p)
                    ).props("flat dense no-caps align=left").classes("w-full")
                for item in files:
                    if mode == MODE_SAVE:
                        ui.button(
                            item.name, icon="description",
                            on_click=lambda p=item: _fill_name(p),
                        ).props("flat dense no-caps align=left").classes("w-full")
                    else:
                        ui.button(
                            item.name, icon="description",
                            on_click=lambda p=item: finish(p),
                        ).props("flat dense no-caps align=left").classes("w-full")
                if not children and not files:
                    ui.label("Nothing here.").classes("text-xs text-grey-7 p-2")

        def _fill_name(path: Path) -> None:
            """Overwriting an existing file is chosen by naming it."""
            if name_input is not None:
                name_input.value = path.name

        with ui.row().classes("w-full justify-between items-center"):
            ui.label("Start from:").classes("text-xs text-grey-7")
            with ui.row().classes("gap-1"):
                for root in roots():
                    ui.button(
                        root.name or str(root), on_click=lambda p=root: go(p)
                    ).props("flat dense no-caps")

        await render()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=lambda: finish(None)).props("flat")
            if mode == MODE_FOLDER:
                ui.button(
                    "Use this folder", on_click=lambda: finish(cursor["path"])
                ).props("color=primary")
            elif mode == MODE_SAVE:
                ui.button(
                    "Save here",
                    on_click=lambda: finish(
                        cursor["path"] / (name_input.value or "config.yaml")
                    ),
                ).props("color=primary")
            else:
                ui.label("Choose a file above.").classes("text-xs text-grey-7 self-center")

    await dialog
    return chosen["path"]


def FolderPicker(  # noqa: N802 - reads as a class at the call site
    label: str, value: str, on_pick: Callable[[Path], None]
) -> PathPicker:
    """A picker for an existing directory."""
    return PathPicker(label, value, on_pick, mode=MODE_FOLDER)


def ConfigFilePicker(  # noqa: N802 - reads as a class at the call site
    label: str, value: str, on_pick: Callable[[Path], None], *, marker: str = ""
) -> PathPicker:
    """A picker for an existing configuration YAML."""
    return PathPicker(
        label,
        value,
        on_pick,
        mode=MODE_OPEN,
        suffixes=CONFIG_SUFFIXES,
        file_type_label="YAML files",
        marker=marker,
    )
