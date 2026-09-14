"""Choosing what the pipeline does, launching it, and watching it.

Every block's form is built when the page loads and then shown or hidden by its
checkbox, rather than created when the block is ticked. Two things follow from that:
loading a config always has a form to write into, and turning a block off and on again
keeps what was typed in it.

The log is filled by polling, not pushed. The process reading the run's output records
lines in a buffer and touches nothing on screen, so a timer owned by this page — and
destroyed with it — asks for whatever arrived since it last looked.
"""
from __future__ import annotations

from typing import Optional

from nicegui import ui
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import BLOCKS, BLOCKS_BY_ID, MS_SHARED_BLOCKS
from enpkg.monolith.pipeline.runner import STAGE_LABELS
from enpkg.monolith.webui import paths, runs, state
from enpkg.monolith.webui.config_files import MS_FORM_KEY
from enpkg.monolith.webui.forms import ModelForm, build_form
from enpkg.monolith.webui.layout import page_frame
from enpkg.monolith.webui.pickers import choose_save_target

# Filled in from the Imports page rather than typed here.
_SIRIUS_INPUT_FIELD = "sirius_params.path_to_input_spectra"


@ui.page("/pipeline", response_timeout=15)
def pipeline_page() -> None:
    with page_frame("/pipeline", "Pipeline"):
        ui.label("Pipeline").classes("text-xl font-bold")

        checkboxes: dict[str, ui.checkbox] = {}
        forms: dict[str, ModelForm] = {}
        dirty = {"value": False}

        def mark_dirty() -> None:
            dirty["value"] = True

        general_form = _general_section(mark_dirty)
        _blocks_section(checkboxes, forms, mark_dirty)
        preview = _preview_section()
        _config_section(checkboxes, forms, general_form)
        launcher = _run_section(checkboxes, forms, general_form)

        def sync() -> None:
            """Persist the forms and revalidate, at most once a second.

            Doing this on every keystroke would run Pydantic over forty fields per
            character and report errors for values half-typed.
            """
            if not dirty["value"]:
                return
            dirty["value"] = False
            _store(checkboxes, forms, general_form)
            preview.refresh()

        ui.timer(1.0, sync)
        ui.timer(0.3, launcher)


# --- sections -----------------------------------------------------------------------


def _general_section(on_change) -> ModelForm:
    """Render GeneralParams once, above the per-block forms."""
    with ui.card().classes("w-full"):
        ui.label("General parameters").classes("font-bold")
        ui.label("Shared by every block that accepts them.").classes(
            "text-sm text-grey-7"
        )
        stored = state.get("general_params") or GeneralParams().model_dump()
        return build_form(GeneralParams, stored, on_change=on_change)


def _blocks_section(
    checkboxes: dict[str, ui.checkbox],
    forms: dict[str, ModelForm],
    on_change,
) -> None:
    """One checkbox per block, and one form per block that has settings."""
    selected = set(state.selected_blocks())
    form_state = state.get("form_state") or {}

    with ui.card().classes("w-full"):
        ui.label("Blocks").classes("font-bold")
        ui.label("Run in the order shown.").classes("text-sm text-grey-7")

        for block in BLOCKS:
            box = ui.checkbox(
                block.label,
                value=block.id in selected,
                on_change=lambda _e, on_change=on_change: on_change(),
            )
            if block.description:
                box.tooltip(block.description)
            checkboxes[block.id] = box

    seen_ms = False
    for block in BLOCKS:
        if block.config_cls is None:
            continue
        if block.id in MS_SHARED_BLOCKS:
            if seen_ms:
                continue
            seen_ms = True
            title = "MS1 / MS2 settings (shared)"
            key = MS_FORM_KEY
            visible_when = [b for b in MS_SHARED_BLOCKS]
        else:
            title = f"{block.label} settings"
            key = block.id
            visible_when = [block.id]

        exclude = {"general_params"}
        if block.id == "sirius":
            exclude.add(_SIRIUS_INPUT_FIELD)

        container = ui.card().classes("w-full")
        with container:
            ui.label(title).classes("font-bold")
            forms[key] = build_form(
                block.config_cls,
                form_state.get(key),
                exclude_fields=exclude,
                on_change=on_change,
            )
        _bind_any(container, [checkboxes[b] for b in visible_when])


def _bind_any(element, boxes: list[ui.checkbox]) -> None:
    """Show ``element`` while any of ``boxes`` is ticked."""
    if len(boxes) == 1:
        element.bind_visibility_from(boxes[0], "value")
        return

    def update() -> None:
        element.visible = any(box.value for box in boxes)

    for box in boxes:
        box.on_value_change(lambda _e: update())
    update()


def _preview_section():
    """Show whether the current settings validate, and what they resolve to."""
    with ui.card().classes("w-full"):
        ui.label("Configuration preview").classes("font-bold")
        body = ui.column().classes("w-full")

    @ui.refreshable
    def preview() -> None:
        body.clear()
        with body:
            try:
                configs, serializer_config = _build_configs()
            except ValidationError as exc:
                ui.label("Configuration is not valid").classes("text-red font-bold")
                ui.code(str(exc)).classes("w-full text-xs")
                return
            except _NoSelection:
                ui.label("Select at least one block.").classes("text-grey-7")
                return
            ui.label("Configuration is valid").classes("text-green font-bold")
            for block_id, cfg in configs.items():
                if cfg is None:
                    continue
                with ui.expansion(BLOCKS_BY_ID[block_id].label).classes("w-full"):
                    ui.code(cfg.model_dump_json(indent=2), language="json").classes(
                        "w-full text-xs"
                    )
            with ui.expansion("Serializer").classes("w-full"):
                ui.code(
                    serializer_config.model_dump_json(indent=2), language="json"
                ).classes("w-full text-xs")

    preview()
    return preview


def _config_section(checkboxes, forms, general_form) -> None:
    """Write the settings to a configuration file.

    Choosing a file and loading it are on the Imports page; this writes what has been
    edited here. The target is named on the button so it is clear where it goes, since
    it was chosen on another page.
    """
    with ui.card().classes("w-full"):
        ui.label("Save configuration").classes("font-bold")
        target = ui.label("").classes("text-sm text-grey-7")

        def refresh_target() -> None:
            target.text = f"Writes to {state.config_path()}"

        refresh_target()

        def collect() -> None:
            # Read the widgets first. The periodic sync would otherwise decide what gets
            # written, so saving straight after a change would store the values from
            # before it.
            _store(checkboxes, forms, general_form)

        def save() -> None:
            collect()
            _save_config(state.config_path())

        async def save_as() -> None:
            collect()
            chosen = await choose_save_target(state.get("config_path", ""))
            if chosen is None:
                return
            state.set_value("config_path", str(chosen))
            refresh_target()
            _save_config(chosen)

        with ui.row().classes("gap-2"):
            ui.button("Save", icon="download", on_click=save).props(
                "no-caps color=primary"
            ).mark("save-button")
            ui.button("Save as…", icon="save_as", on_click=save_as).props(
                "flat no-caps"
            ).mark("save-as-button")

        # The target is chosen on another page, so it can change while this one is open.
        ui.timer(2.0, refresh_target)


def _run_section(checkboxes, forms, general_form):
    """The Run and Cancel buttons, the live log, and the finished-run summary."""
    with ui.card().classes("w-full"):
        ui.label("Execution").classes("font-bold")

        with ui.row().classes("items-center gap-2"):
            run_button = (
                ui.button("Run", icon="play_arrow")
                .props("color=primary no-caps")
                .mark("run-button")
            )
            cancel_button = (
                ui.button("Cancel", icon="stop")
                .props("color=negative no-caps")
                .mark("cancel-button")
            )
            verbose = ui.checkbox(
                "Verbose",
                value=bool(state.get("verbose")),
                on_change=lambda e: state.set_value("verbose", e.value),
            )
            status = ui.label("").classes("text-sm")

        log = ui.log(max_lines=runs.MAX_BUFFERED_LINES).classes(
            "w-full h-64 text-xs font-mono"
        )
        results = ui.column().classes("w-full")

        cursor = {"value": 0, "run_id": None}

        async def on_run() -> None:
            # Preparing is synchronous so a rejected run reports why immediately, rather
            # than depending on a task being scheduled first.
            handle = _prepare_run(
                checkboxes, forms, general_form, bool(verbose.value), log, cursor
            )
            if handle is None:
                return
            try:
                await runs.start(handle)
            except Exception as exc:  # noqa: BLE001
                handle.status = "failed"
                handle.error = f"Could not start the run: {exc}"
                ui.notify(handle.error, type="negative")

        run_button.on_click(on_run)
        cancel_button.on_click(_cancel)

        def poll() -> None:
            handle = state.session().run or state.session().last_run
            run_button.set_enabled(handle is None or not handle.is_active)
            cancel_button.set_enabled(handle is not None and handle.is_active)
            if handle is None:
                status.text = "Nothing has run yet."
                return

            # A run started before this page was opened has output the log has never
            # seen, so the cursor restarts whenever the handle changes.
            if cursor["run_id"] != handle.id:
                cursor["run_id"] = handle.id
                cursor["value"] = 0
                log.clear()

            pending, cursor["value"] = handle.lines_since(cursor["value"])
            for line in pending:
                log.push(line)

            status.text = runs.summary_line(handle)
            if not handle.is_active and state.session().run is handle:
                state.session().last_run = handle
                state.session().run = None
                _render_results(results, handle)

        return poll


# --- actions ------------------------------------------------------------------------


class _NoSelection(Exception):
    """Raised when nothing is ticked, which is not a validation error."""


def _current_selection(checkboxes: dict[str, ui.checkbox]) -> list[str]:
    return [block.id for block in BLOCKS if checkboxes[block.id].value]


def _build_configs():
    """Validate the stored settings into config objects.

    Reads the store rather than the widgets, so it works from a page that did not build
    them. Callers that have just edited widgets call ``_store`` first.
    """
    stored = state.get("form_state") or {}
    selected = state.selected_blocks()
    if not selected:
        raise _NoSelection

    raw = {
        block_id: dict(
            stored.get(MS_FORM_KEY if block_id in MS_SHARED_BLOCKS else block_id) or {}
        )
        for block_id in selected
    }
    shared = state.get("general_params") or GeneralParams().model_dump()
    configs = config_io.build_configs(selected, config_io.inject_shared_params(raw, shared))
    serializer_config = SerializerConfig.model_validate(state.get("serializer") or {})
    return configs, serializer_config


def _store(checkboxes, forms, general_form) -> None:
    """Copy what is on screen into the stored session values."""
    state.set_value("selected_blocks", _current_selection(checkboxes))
    state.set_value("general_params", general_form.get_values())
    state.set_value(
        "form_state", {key: form.get_values() for key, form in forms.items()}
    )


def _save_config(path) -> None:
    """Write the current settings to a config file."""
    try:
        configs, serializer_config = _build_configs()
    except _NoSelection:
        ui.notify("Select at least one block first.", type="warning")
        return
    except ValidationError as exc:
        ui.notify(f"Fix the configuration first:\n{exc}", type="negative")
        return
    try:
        config_io.save_unified_yaml(path, configs, serializer=serializer_config)
    except OSError as exc:
        ui.notify(f"Could not write {path}: {exc}", type="negative")
        return
    ui.notify(f"Saved {path}", type="positive")


def _prepare_run(checkboxes, forms, general_form, verbose, log, cursor):
    """Validate everything and return a ready-to-start handle, or None with a reason."""
    session = state.session()
    if session.run is not None and session.run.is_active:
        ui.notify("A run is already going.", type="warning")
        return None

    _store(checkboxes, forms, general_form)
    try:
        configs, serializer_config = _build_configs()
    except _NoSelection:
        ui.notify("Select at least one block.", type="warning")
        return None
    except ValidationError as exc:
        ui.notify(f"Fix the configuration first:\n{exc}", type="negative")
        return None

    mode = state.get("mode", "single")
    kind = "batch" if mode == "batch" else "run"

    problem = _missing_inputs(kind, state.selected_blocks())
    if problem:
        ui.notify(problem, type="warning")
        return None

    handle = runs.new_handle(kind, paths.RUNS_DIR)
    config_io.save_unified_yaml(handle.config_path, configs, serializer=serializer_config)

    if kind == "run":
        handle.argv = runs.build_argv(
            handle,
            spectra=state.resolved_input("spectra"),
            metadata=state.resolved_input("metadata"),
            quant=state.resolved_input("quant"),
            sirius_spectra=state.resolved_input("sirius_spectra")
            if "sirius" in state.selected_blocks()
            else None,
            verbose=verbose,
        )
    else:
        handle.argv = runs.build_argv(
            handle, parent_dir=state.batch_path().resolve(), verbose=verbose
        )

    log.clear()
    cursor["value"] = 0
    cursor["run_id"] = handle.id
    session.run = handle
    session.last_run = None
    return handle


def _missing_inputs(kind: str, selected: list[str]) -> Optional[str]:
    """Return what the Imports page still needs to supply, or None."""
    if kind == "batch":
        if not state.batch_path().is_dir():
            return "Choose a batch parent folder on the Imports page."
        return None

    missing = [
        name for name in ("spectra", "metadata", "quant") if not state.get(name)
    ]
    if missing:
        return f"Choose {', '.join(missing)} on the Imports page."
    if "sirius" in selected and not state.get("sirius_spectra"):
        return "The SIRIUS block is selected; choose its spectra file on the Imports page."
    return None


async def _cancel() -> None:
    handle = state.session().run
    if handle is None or not handle.is_active:
        return
    await runs.cancel(handle)
    ui.notify("Stopping the run…", type="warning")


def _render_results(container: ui.column, handle) -> None:
    """Show what a finished run produced."""
    container.clear()
    result = handle.result
    with container:
        if handle.status == "cancelled":
            ui.label("Cancelled — any output is incomplete.").classes("text-grey-7")
            return
        if result is None:
            ui.label(handle.error or "The run wrote no result file.").classes("text-red")
            return

        summary = result.get("summary")
        if summary:
            with ui.row().classes("gap-6"):
                for label, key in (
                    ("Spectra", "n_spectra"),
                    ("OTT matches", "n_ott_matches"),
                    ("Network nodes", "n_network_nodes"),
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(str(summary.get(key, 0))).classes("text-xl font-bold")
                        ui.label(label).classes("text-xs text-grey-7")

        if result.get("kind") == "batch":
            ui.label(
                f"{result.get('n_succeeded', 0)} succeeded, {result.get('n_failed', 0)} failed"
            ).classes("text-sm")

        durations = result.get("durations") or {}
        if durations:
            with ui.expansion("Timings").classes("w-full"):
                for key, seconds in sorted(
                    durations.items(), key=lambda kv: kv[1], reverse=True
                ):
                    label = STAGE_LABELS.get(
                        key, BLOCKS_BY_ID[key].label if key in BLOCKS_BY_ID else key
                    )
                    ui.label(f"{label}: {seconds:.1f}s").classes("text-sm font-mono")

        for label, key in (
            ("Log", "log_file"),
            ("Summary", "summary_file"),
            ("Graph", "ttl_file"),
        ):
            value = result.get(key)
            if value:
                ui.label(f"{label}: {value}").classes("text-xs font-mono text-grey-7")
