"""Build an input form from a Pydantic config model.

Widgets here are persistent Python objects: one is created per field when the page is
built, and it keeps its identity for the lifetime of that page. Loading a saved config is
therefore an assignment to each widget's ``value`` — there is no separate mechanism for
getting a value onto the screen.

:meth:`ModelForm.get_values` returns a plain nested dict, so Pydantic remains the only
thing that validates: the widgets narrow what can be typed, and
``model_cls.model_validate`` decides what is actually acceptable.

A config model holds two kinds of field. ``duckdb_path`` is a string and becomes one
widget; ``general_params`` is itself a model and becomes a nested set of widgets inside
an expansion. Both are stored in :attr:`ModelForm.fields` because both answer ``get()``
and ``set()`` — a nested model's ``get()`` returns its own dict, which is exactly the
shape the parent needs to hand to ``model_validate``.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, get_origin

from nicegui import ui
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from enpkg.monolith.configuration.introspect import (
    default_for,
    enum_choices,
    is_basemodel,
    number_bounds,
    unwrap_optional,
)

# A string longer than this, or one containing a newline, gets a multi-line box.
_TEXTAREA_THRESHOLD = 80


class FieldHandle:
    """Reads and writes one field's widget."""

    def __init__(self, getter: Callable[[], Any], setter: Callable[[Any], None]) -> None:
        self._getter = getter
        self._setter = setter

    def get(self) -> Any:
        return self._getter()

    def set(self, value: Any) -> None:
        self._setter(value)


class ModelForm:
    """The widgets for one Pydantic model, addressable as a whole.

    Satisfies the same ``get``/``set`` pair as :class:`FieldHandle`, so a model nested
    inside another model is just another entry in the parent's :attr:`fields`.
    """

    def __init__(self, model_cls: type[BaseModel]) -> None:
        self.model_cls = model_cls
        self.fields: dict[str, FieldHandle | ModelForm] = {}

    def get(self) -> dict[str, Any]:
        return self.get_values()

    def set(self, value: Any) -> None:
        self.set_values(value if isinstance(value, dict) else None)

    def get_values(self) -> dict[str, Any]:
        """Return the current values, shaped for ``model_cls.model_validate``."""
        return {name: handle.get() for name, handle in self.fields.items()}

    def set_values(self, data: Optional[dict[str, Any]]) -> None:
        """Overwrite every widget from ``data``, filling absences with field defaults.

        Every field is written, not only the ones ``data`` mentions. A partial write
        would leave whatever the previous config put there, so loading a file that omits
        a setting would silently keep the old value and the file would no longer describe
        the run it produces.
        """
        data = data or {}
        for name, handle in self.fields.items():
            field_info = self.model_cls.model_fields[name]
            handle.set(data.get(name, default_for(field_info)))


def build_form(
    model_cls: type[BaseModel],
    current: Optional[dict[str, Any]] = None,
    *,
    exclude_fields: Optional[set[str]] = None,
    on_change: Optional[Callable[[], None]] = None,
) -> ModelForm:
    """Create widgets for ``model_cls`` in the current container and return a handle.

    ``exclude_fields`` accepts dotted paths (``sirius_params.path_to_input_spectra``) for
    a field of a nested model that is filled in from elsewhere and must not be editable
    here.
    """
    current = current or {}
    exclude_fields = exclude_fields or set()
    form = ModelForm(model_cls)

    for name, field_info in model_cls.model_fields.items():
        if name in exclude_fields:
            continue
        nested_exclude = {
            path[len(name) + 1 :] for path in exclude_fields if path.startswith(f"{name}.")
        }
        value = current.get(name, default_for(field_info))
        annotation, _ = unwrap_optional(field_info.annotation)

        if is_basemodel(annotation):
            with ui.expansion(_pretty(name)).classes("w-full"):
                form.fields[name] = build_form(
                    annotation,
                    value if isinstance(value, dict) else None,
                    exclude_fields=nested_exclude or None,
                    on_change=on_change,
                )
            continue

        form.fields[name] = _build_field(name, field_info, value, on_change)

    return form


def _pretty(name: str) -> str:
    """Turn a field name into a label."""
    return name.replace("_", " ")


def _build_field(
    name: str,
    field_info: FieldInfo,
    value: Any,
    on_change: Optional[Callable[[], None]],
) -> FieldHandle:
    """Create the widget for one non-model field."""
    annotation, optional = unwrap_optional(field_info.annotation)
    label = _pretty(name)
    hint = field_info.description or ""

    choices = enum_choices(annotation, field_info)
    if choices:
        element = ui.select(
            choices,
            label=label,
            value=value if value in choices else choices[0],
            on_change=_wrap(on_change),
        ).classes("w-full")
        _tooltip(element, hint)

        def set_choice(new: Any, element=element, choices=choices) -> None:
            # Quasar renders a value outside the option list as an empty box, which reads
            # as data loss. A config naming a retired member keeps the default instead.
            element.value = new if new in choices else choices[0]

        return FieldHandle(lambda element=element: element.value, set_choice)

    if annotation is bool:
        element = ui.checkbox(label, value=bool(value), on_change=_wrap(on_change))
        _tooltip(element, hint)
        return FieldHandle(
            lambda element=element: bool(element.value),
            lambda new, element=element: setattr(element, "value", bool(new)),
        )

    if annotation in (int, float):
        low, high = number_bounds(field_info)
        is_int = annotation is int
        element = ui.number(
            label=label,
            value=value,
            min=low,
            max=high,
            precision=0 if is_int else None,
            step=1 if is_int else None,
            on_change=_wrap(on_change),
        ).classes("w-full")
        _tooltip(element, hint)
        return FieldHandle(
            lambda element=element, is_int=is_int: _number_value(element.value, is_int),
            lambda new, element=element: setattr(element, "value", new),
        )

    if get_origin(annotation) in (list, tuple):
        element = ui.textarea(
            label=f"{label} (one per line)",
            value="\n".join(str(item) for item in (value or [])),
            on_change=_wrap(on_change),
        ).classes("w-full")
        _tooltip(element, hint)
        return FieldHandle(
            lambda element=element: [
                line.strip() for line in (element.value or "").splitlines() if line.strip()
            ],
            lambda new, element=element: setattr(
                element, "value", "\n".join(str(item) for item in (new or []))
            ),
        )

    text = "" if value is None else str(value)
    factory = ui.textarea if (len(text) > _TEXTAREA_THRESHOLD or "\n" in text) else ui.input
    element = factory(label=label, value=text, on_change=_wrap(on_change)).classes("w-full")
    _tooltip(element, hint)
    return FieldHandle(
        lambda element=element, optional=optional: _text_value(element.value, optional),
        lambda new, element=element: setattr(element, "value", "" if new is None else str(new)),
    )


def _number_value(raw: Any, is_int: bool) -> Any:
    """Return a number, or None when the box is empty.

    An empty box means "unset", which matters for the serializer's
    ``max_ions_per_spectrum``: it is used as a slice bound, so substituting 0 emits no
    ions at all rather than leaving them uncapped. A field that is required rather than
    optional also yields None, so Pydantic reports the omission instead of the form
    inventing a number.
    """
    if raw is None or raw == "":
        return None
    return int(raw) if is_int else float(raw)


def _text_value(raw: Any, optional: bool) -> Any:
    """Return the text, mapping an empty box to None for an optional field."""
    if raw is None:
        return None
    text = str(raw)
    if not text and optional:
        return None
    return text


def _tooltip(element: Any, hint: str) -> None:
    if hint:
        element.tooltip(hint)


def _wrap(on_change: Optional[Callable[[], None]]) -> Optional[Callable[..., None]]:
    """Adapt a no-argument callback to the value-change event signature."""
    if on_change is None:
        return None

    def handler(_event: Any = None) -> None:
        on_change()

    return handler
