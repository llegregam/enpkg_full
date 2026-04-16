"""Render a Streamlit form for any Pydantic BaseModel class.

The returned value is a plain dict compatible with ``model_cls.model_validate``,
so validation and coercion are delegated entirely to Pydantic.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Union, get_args, get_origin

import streamlit as st
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined


def render_model(
    model_cls: type[BaseModel],
    current: dict | None,
    key_prefix: str,
    exclude_fields: set[str] | None = None,
) -> dict:
    """
    Render a Streamlit form for a given Pydantic BaseModel class and return the user-provided values.

    This function dynamically generates Streamlit widgets for each field in the provided Pydantic BaseModel class.
    It allows users to input values for the fields, which are then returned as a dictionary compatible with
    Pydantic's validation methods (e.g., `model_cls.model_validate`). The function supports pre-populating fields
    with existing values and excluding specific fields from rendering.

    Args:
        model_cls (type[BaseModel]): 
            The Pydantic BaseModel subclass (e.g., `EnhancerConfig`) to render in the GUI. Each field in the model
            is rendered as a corresponding Streamlit widget based on its type and metadata.
        current (dict | None): 
            A dictionary of current values to pre-populate the widgets with. This is useful for editing existing
            configurations or loading values from a YAML file. If `None`, fields will use their default values.
        key_prefix (str): 
            A unique string prefix to ensure Streamlit widget keys are distinct across the form. This is typically
            the block ID (e.g., "ms1") to avoid key collisions in Streamlit's session state.
        exclude_fields (set[str] | None, optional): 
            A set of field names to exclude from rendering. This is useful for cases where certain fields (e.g.,
            shared sub-configurations like `general_params`) are rendered separately. Defaults to `None`.
    
    Returns:
        dict: 
            A dictionary mapping field names to the current values of the corresponding widgets. This dictionary
            can be validated and coerced using `model_cls.model_validate`.
    """
    current = current or {}
    exclude_fields = exclude_fields or set()
    out: dict[str, Any] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_name in exclude_fields:
            continue
        out[field_name] = _render_field(
            field_name,
            field_info,
            current.get(field_name),
            f"{key_prefix}.{field_name}",
        )
    return out


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_optional) for Optional[T] / T | None."""
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _is_basemodel(tp: Any) -> bool:
    try:
        return isinstance(tp, type) and issubclass(tp, BaseModel)
    except TypeError:
        return False


def _number_bounds(field_info: FieldInfo) -> tuple[Optional[float], Optional[float]]:
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


def _pattern_choices(field_info: FieldInfo) -> Optional[list[str]]:
    """Extract a small enum-like choice list from a simple regex pattern.

    Recognises ``^(a|b|c)$`` style patterns that Pydantic validators use for
    string enums such as polarity.
    """
    for m in field_info.metadata:
        pattern = getattr(m, "pattern", None)
        if pattern and isinstance(pattern, str):
            match = re.fullmatch(r"\^\(([^()]+)\)\$", pattern)
            if match:
                return match.group(1).split("|")
    return None


def _render_field(
    name: str,
    field_info: FieldInfo,
    current_value: Any,
    key: str,
) -> Any:
    """
    Render a Streamlit widget for a single Pydantic field.

    This function generates a Streamlit widget based on the type and metadata of a given Pydantic field.
    It supports various field types, including nested BaseModel instances, lists, booleans, integers, floats,
    and strings. The rendered widget allows users to input or modify the field's value, which is then returned
    for further processing or validation. 

    Args:
        name (str): 
            The name of the field to render. This is used as the label for the widget.
        field_info (FieldInfo): 
            The Pydantic `FieldInfo` object containing metadata about the field, such as its type, default value,
            description, and validation constraints.
        current_value (Any): 
            The current value of the field, used to pre-populate the widget. If `None`, the field's default value
            (if defined) is used instead.
        key (str): 
            A unique key for the Streamlit widget. This ensures that the widget's state is properly managed
            within Streamlit's session state.

    Returns:
        Any: 
            The value entered by the user in the widget. The type of the returned value depends on the field's type:
            - For nested BaseModel fields, a dictionary of values is returned.
            - For lists, a list of strings is returned.
            - For booleans, integers, and floats, the corresponding primitive type is returned.
            - For strings, either a free-form text input or a dropdown (if choices are defined) is returned.
    """

    annotation, _ = _unwrap_optional(field_info.annotation)
    help_text = field_info.description or ""
    default = current_value if current_value is not None else _default_for(field_info)

    if _is_basemodel(annotation):
        with st.expander(name, expanded=False):
            nested_current = current_value if isinstance(current_value, dict) else (
                default if isinstance(default, dict) else {}
            )
            return render_model(annotation, nested_current, key)

    origin = get_origin(annotation)
    if origin in (list, tuple):
        raw = st.text_area(
            name,
            value="\n".join(str(x) for x in (default or [])),
            key=key,
            help=help_text + "\n(One value per line)",
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]

    if annotation is bool:
        return st.checkbox(name, value=bool(default) if default is not None else False, key=key, help=help_text)

    if annotation is int:
        lo, hi = _number_bounds(field_info)
        return st.number_input(
            name,
            value=int(default) if default is not None else 0,
            min_value=int(lo) if lo is not None else None,
            max_value=int(hi) if hi is not None else None,
            step=1,
            key=key,
            help=help_text,
        )

    if annotation is float:
        lo, hi = _number_bounds(field_info)
        return st.number_input(
            name,
            value=float(default) if default is not None else 0.0,
            min_value=float(lo) if lo is not None else None,
            max_value=float(hi) if hi is not None else None,
            step=0.01,
            format="%.6g",
            key=key,
            help=help_text,
        )

    if annotation is str:
        choices = _pattern_choices(field_info)
        if choices:
            idx = choices.index(default) if default in choices else 0
            return st.selectbox(name, choices, index=idx, key=key, help=help_text)
        value = "" if default is None else str(default)
        if len(value) > 80 or "\n" in value:
            return st.text_area(name, value=value, key=key, help=help_text)
        return st.text_input(name, value=value, key=key, help=help_text)

    # Fallback: free-form text, post-validated by Pydantic.
    return st.text_input(name, value="" if default is None else str(default), key=key, help=help_text)


def _default_for(field_info: FieldInfo) -> Any:
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
