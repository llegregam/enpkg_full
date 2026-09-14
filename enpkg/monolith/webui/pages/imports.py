"""Choosing the data a run will read.

Two shapes of input. A single experiment is three files in one folder: spectra, sample
metadata and a quantification table (dataset centric approach). A batch is a parent folder holding one shared
metadata file and a subfolder per experiment, which the pipeline discovers for itself.

Scanning a folder is blocking file access and can be slow on a network share, while a
page builder has a few seconds before the client gives up on it. So the page renders its
controls immediately and fills in the file lists from a background task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from nicegui import background_tasks, run, ui

from enpkg.monolith.pipeline.batch_runner import (
    METADATA_SUFFIXES,
    QUANT_SUFFIXES,
    SPECTRA_SUFFIXES,
    discover_experiments,
    find_shared_metadata,
)
from enpkg.monolith.webui import state
from enpkg.monolith.webui.folder_picker import FolderPicker
from enpkg.monolith.webui.layout import page_frame

# Which stored key each dropdown fills, and what it accepts. Metadata and quant overlap
# on .csv, so the order within each tuple is a preference: the first suffix with a match
# wins, which is what stops a quant table being taken for the metadata file.
_SINGLE_INPUTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("spectra", "Spectra", SPECTRA_SUFFIXES),
    ("metadata", "Sample metadata", METADATA_SUFFIXES),
    ("quant", "Quantification table", QUANT_SUFFIXES),
]


def files_matching(folder: Path, suffixes: tuple[str, ...]) -> list[str]:
    """Return the names of files in ``folder`` with any of ``suffixes``, preferred first.

    Within a suffix the order is alphabetical; suffixes earlier in the tuple come first,
    so the default selection follows the same preference the pipeline's own discovery
    uses.
    """
    if not folder.is_dir():
        return []
    names: list[str] = []
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_file())
    except (OSError, PermissionError):
        return []
    for suffix in suffixes:
        names += [p.name for p in entries if p.suffix.lower() == suffix]
    return names


def sirius_candidates(folder: Path) -> list[str]:
    """Return spectra files that look like SIRIUS input."""
    return [
        name
        for name in files_matching(folder, SPECTRA_SUFFIXES)
        if "_sirius" in name.lower()
    ]


@ui.page("/")
def index() -> None:
    ui.navigate.to("/imports")


@ui.page("/imports", response_timeout=15)
def imports_page() -> None:
    with page_frame("/imports", "Imports"):
        ui.label("Input data").classes("text-xl font-bold")

        mode = ui.toggle(
            {"single": "Single experiment", "batch": "Batch"},
            value=state.get("mode", "single"),
            on_change=lambda e: _set_mode(e.value),
        ).props("no-caps")

        single = ui.column().classes("w-full gap-3")
        batch = ui.column().classes("w-full gap-3")
        single.bind_visibility_from(mode, "value", backward=lambda v: v == "single")
        batch.bind_visibility_from(mode, "value", backward=lambda v: v == "batch")

        with single:
            _single_section()
        with batch:
            _batch_section()


def _set_mode(value: str) -> None:
    state.set_value("mode", value)


def _single_section() -> None:
    """Folder picker plus one dropdown per input file."""
    with ui.card().classes("w-full"):
        ui.label("Experiment folder").classes("font-bold")
        selects: dict[str, ui.select] = {}

        def refresh_lists(folder: Path) -> None:
            background_tasks.create(_populate(folder, selects), name="enpkg-scan-input")

        picker = FolderPicker(
            "Folder holding the spectra, metadata and quant files",
            state.get("input_dir", ""),
            on_pick=lambda path: (
                state.set_value("input_dir", str(path)),
                refresh_lists(path),
            ),
        )

        # Built with no options and no value. The options come from scanning the folder,
        # which happens after the page is delivered, and a select rejects a value that is
        # not among its options -- so a previously chosen filename passed in here would
        # raise as soon as the page was revisited. `_populate` sets both together.
        for key, label, _suffixes in _SINGLE_INPUTS:
            selects[key] = (
                ui.select([], label=label, with_input=True, clearable=True)
                .props("dense")
                .classes("w-full")
                .on_value_change(lambda e, key=key: state.set_value(key, e.value))
            )

        selects["sirius_spectra"] = (
            ui.select(
                [],
                label="Spectra for SIRIUS (only needed when that block runs)",
                with_input=True,
                clearable=True,
            )
            .props("dense")
            .classes("w-full")
            .on_value_change(lambda e: state.set_value("sirius_spectra", e.value))
        )

        ui.button("Rescan", icon="refresh", on_click=lambda: refresh_lists(state.input_path())).props(
            "flat dense no-caps"
        )

        refresh_lists(state.input_path())
        _ = picker


async def _populate(folder: Path, selects: dict[str, ui.select]) -> None:
    """Fill the dropdowns from a folder scan, keeping any still-valid selection."""
    scans = {
        key: await run.io_bound(files_matching, folder, suffixes)
        for key, _label, suffixes in _SINGLE_INPUTS
    }
    scans["sirius_spectra"] = await run.io_bound(sirius_candidates, folder)

    for key, names in scans.items():
        element = selects.get(key)
        if element is None:
            continue
        current = state.get(key)
        # A file chosen before the folder changed may no longer exist. Falling back to
        # the first match keeps the page usable; clearing it keeps it honest when there
        # is nothing to fall back to.
        chosen = current if current in names else (names[0] if names else None)
        # Options and value are set in one call: a select rejects a value that is not
        # among its options, so assigning them separately depends on the order.
        element.set_options(names, value=chosen)
        state.set_value(key, chosen)


def _batch_section() -> None:
    """Folder picker plus a preview of the experiments a run would pick up."""
    with ui.card().classes("w-full"):
        ui.label("Batch parent folder").classes("font-bold")
        ui.label(
            "One shared metadata file at the top, one subfolder per experiment."
        ).classes("text-sm text-grey-7")

        metadata_label = ui.label("").classes("text-sm")
        table = ui.table(
            columns=[
                {"name": "run_name", "label": "Run", "field": "run_name", "align": "left"},
                {"name": "folder", "label": "Subfolder", "field": "folder", "align": "left"},
                {"name": "sirius", "label": "SIRIUS input", "field": "sirius", "align": "left"},
            ],
            rows=[],
            row_key="run_name",
        ).classes("w-full")

        def refresh(folder: Path) -> None:
            background_tasks.create(
                _preview(folder, metadata_label, table), name="enpkg-scan-batch"
            )

        FolderPicker(
            "Parent folder",
            state.get("batch_dir", ""),
            on_pick=lambda path: (
                state.set_value("batch_dir", str(path)),
                refresh(path),
            ),
        )
        ui.button("Rescan", icon="refresh", on_click=lambda: refresh(state.batch_path())).props(
            "flat dense no-caps"
        )
        refresh(state.batch_path())


async def _preview(folder: Path, metadata_label: ui.label, table: ui.table) -> None:
    """Show the shared metadata file and the discovered experiments."""
    metadata: Optional[Path] = await run.io_bound(_safe_metadata, folder)
    experiments = await run.io_bound(_safe_discover, folder)

    if metadata is None:
        metadata_label.text = (
            f"No shared metadata file found (looked for {', '.join(METADATA_SUFFIXES)})."
        )
        metadata_label.classes(replace="text-sm text-red")
    else:
        metadata_label.text = f"Shared metadata: {metadata.name}"
        metadata_label.classes(replace="text-sm text-grey-7")

    table.rows = [
        {
            "run_name": exp.run_name,
            "folder": exp.subfolder.name,
            "sirius": "yes" if exp.sirius_spectra_path else "—",
        }
        for exp in experiments
    ]
    table.update()


def _safe_metadata(folder: Path) -> Optional[Path]:
    if not folder.is_dir():
        return None
    try:
        return find_shared_metadata(folder)
    except OSError:
        return None


def _safe_discover(folder: Path):
    if not folder.is_dir():
        return []
    try:
        return discover_experiments(folder)
    except OSError:
        return []
