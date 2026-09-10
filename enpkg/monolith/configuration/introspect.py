"""Read the shape of a Pydantic config model without instantiating it.

A GUI that builds its input widgets from the config models needs to answer questions like
"is this field a bounded number?" or "is this field a small set of allowed strings?" before
any value exists. Pydantic records those answers on ``model_cls.model_fields``, but spread
across three places: the type annotation, a list of constraint objects in
``FieldInfo.metadata``, and the default (which may be a value or a factory). The helpers
here read all three and return plain Python values.

Both front ends import this module, so a field type that renders correctly in one renders
correctly in the other. Nothing here imports a UI toolkit, which is also what makes it
testable without starting a server.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined


def unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return ``(inner_type, is_optional)`` for ``Optional[T]`` / ``T | None``.

    A widget for ``Optional[float]`` looks the same as a widget for ``float``; the only
    difference is whether leaving it empty is legal. Callers therefore render against the
    inner type and use the flag to decide how to treat an empty input.
    """
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def is_basemodel(tp: Any) -> bool:
    """Return whether ``tp`` is a Pydantic model class, i.e. a nested config section."""
    try:
        return isinstance(tp, type) and issubclass(tp, BaseModel)
    except TypeError:
        return False


def number_bounds(field_info: FieldInfo) -> tuple[Optional[float], Optional[float]]:
    """Return ``(low, high)`` from a numeric field's ``ge``/``gt``/``le``/``lt`` constraints.

    Pydantic stores these as separate constraint objects in ``field_info.metadata`` rather
    than as attributes of the field, so they have to be collected by scanning that list.
    Either bound is ``None`` when the field does not constrain that side.

    The strict and non-strict forms are deliberately collapsed: ``gt`` is reported as the
    same lower bound as ``ge``. A widget bound is only a pre-filter, and Pydantic still
    rejects a value that sits exactly on an exclusive bound.
    """
    lo = hi = None
    for m in field_info.metadata:
        for attr, target in (("ge", "lo"), ("gt", "lo"), ("le", "hi"), ("lt", "hi")):
            val = getattr(m, attr, None)
            if val is not None:
                if target == "lo":
                    lo = float(val)
                else:
                    hi = float(val)
    return lo, hi


def pattern_choices(field_info: FieldInfo) -> Optional[list[str]]:
    """Return the alternatives of a ``^(a|b|c)$`` regex constraint, or ``None``.

    Some string fields express a fixed set of allowed values as a regex rather than as a
    ``Literal`` — ``ionization_mode`` uses ``^(pos|neg)$``. Recognising that shape lets such
    a field render as a dropdown instead of a free-text box. Any regex that is not exactly
    this shape returns ``None``, because a partial reading of a pattern would be worse than
    no reading at all.
    """
    for m in field_info.metadata:
        pattern = getattr(m, "pattern", None)
        if pattern and isinstance(pattern, str):
            match = re.fullmatch(r"\^\(([^()]+)\)\$", pattern)
            if match:
                return match.group(1).split("|")
    return None


def enum_choices(annotation: Any, field_info: FieldInfo) -> Optional[list[str]]:
    """Return the allowed values of a field restricted to a fixed set, or ``None``.

    Covers both ways this project expresses such a restriction: a ``Literal[...]``
    annotation, and a ``^(a|b)$`` regex constraint on a plain ``str``. A field that is
    neither returns ``None`` and is rendered as free text.

    ``Optional`` is unwrapped first, so ``Optional[Literal["a", "b"]]`` reports the same
    choices as ``Literal["a", "b"]``.
    """
    inner, _ = unwrap_optional(annotation)
    if get_origin(inner) is Literal:
        return [str(arg) for arg in get_args(inner)]
    return pattern_choices(field_info)


def default_for(field_info: FieldInfo) -> Any:
    """Return the value a field starts at when no saved value is supplied.

    Three cases, in order: a plain default, a ``default_factory`` (used for nested config
    sections, which cannot share one mutable instance across models), and no default at all.
    A factory that raises yields ``None`` rather than propagating, so one unconstructible
    field cannot stop the rest of a form from rendering.

    A nested model is returned as a dict via ``model_dump()`` so the caller receives the
    same shape whether the default came from a factory or from saved YAML.
    """
    default = field_info.default
    if default is not PydanticUndefined and default is not None:
        return default
    factory = field_info.default_factory
    if factory is not None:
        try:
            value = factory()  # type: ignore[call-arg]
        except Exception:
            return None
        if isinstance(value, BaseModel):
            return value.model_dump()
        return value
    return None
