"""Options controlling what the RDF export contains.

Deliberately thin: a form over :class:`SerializerConfig` and nothing else. Because the
form is generated from the model's fields, a new option becomes available here by adding
a field to that model, and grouping related options means nesting a model inside it,
which renders as its own expandable section.
"""
from __future__ import annotations

from nicegui import ui
from pydantic import ValidationError

from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.webui import state
from enpkg.monolith.webui.forms import build_form
from enpkg.monolith.webui.layout import page_frame


@ui.page("/serializer")
def serializer_page() -> None:
    with page_frame("/serializer", "Serializer"):
        ui.label("RDF serializer").classes("text-xl font-bold")
        ui.label(
            "Applied when a run exports its knowledge graph. Saved with the rest of the "
            "configuration, under the `serializer` key."
        ).classes("text-sm text-grey-7")

        dirty = {"value": False}
        status = ui.label("").classes("text-sm")

        with ui.card().classes("w-full"):
            form = build_form(
                SerializerConfig,
                state.get("serializer") or SerializerConfig().model_dump(),
                on_change=lambda: dirty.__setitem__("value", True),
            )

        with ui.row().classes("gap-2"):

            def reset() -> None:
                defaults = SerializerConfig().model_dump()
                form.set_values(defaults)
                state.set_value("serializer", defaults)
                status.text = "Reset to defaults."
                status.classes(replace="text-sm text-grey-7")

            ui.button("Reset to defaults", icon="restart_alt", on_click=reset).props(
                "flat no-caps"
            )

        ui.label(
            "The molecular-network edges are not an option here: whether they are "
            "emitted follows from whether the networking block is part of the run."
        ).classes("text-xs text-grey-7")

        def sync() -> None:
            if not dirty["value"]:
                return
            dirty["value"] = False
            values = form.get_values()
            try:
                validated = SerializerConfig.model_validate(values)
            except ValidationError as exc:
                status.text = _first_error(exc)
                status.classes(replace="text-sm text-red")
                return
            state.set_value("serializer", validated.model_dump())
            status.text = "Saved."
            status.classes(replace="text-sm text-green")

        ui.timer(1.0, sync)


def _first_error(exc: ValidationError) -> str:
    """Return one readable line from a validation failure."""
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"]) or "value"
    return f"{field}: {first['msg']}"
