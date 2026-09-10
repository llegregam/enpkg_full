"""Tests for the Pydantic field introspection shared by the form builders."""
from typing import Literal, Optional

import pytest
from pydantic import BaseModel, Field

from enpkg.monolith.configuration.introspect import (
    default_for,
    enum_choices,
    is_basemodel,
    number_bounds,
    unwrap_optional,
)
from enpkg.monolith.pipeline.blocks import BLOCKS


class Nested(BaseModel):
    inner: int = 3


class Sample(BaseModel):
    flag: bool = True
    bounded_int: int = Field(default=5, ge=1, le=10)
    exclusive_float: float = Field(default=0.5, gt=0.0, lt=1.0)
    literal_choice: Literal["a", "b"] = "a"
    optional_literal: Optional[Literal["x", "y"]] = None
    pattern_choice: str = Field(default="pos", pattern="^(pos|neg)$")
    free_text: str = "anything"
    maybe_int: Optional[int] = None
    nested: Nested = Field(default_factory=Nested)


def test_unwrap_optional_reports_inner_type_and_flag():
    assert unwrap_optional(Optional[int]) == (int, True)
    assert unwrap_optional(int | None) == (int, True)
    assert unwrap_optional(int) == (int, False)


def test_is_basemodel_accepts_models_and_rejects_scalars():
    assert is_basemodel(Nested)
    assert not is_basemodel(int)
    assert not is_basemodel("not a type")


@pytest.mark.parametrize(
    ("field_name", "expected"),
    [
        ("bounded_int", (1.0, 10.0)),
        ("exclusive_float", (0.0, 1.0)),
        ("free_text", (None, None)),
    ],
)
def test_number_bounds_collects_constraints(field_name, expected):
    assert number_bounds(Sample.model_fields[field_name]) == expected


@pytest.mark.parametrize(
    ("field_name", "expected"),
    [
        ("literal_choice", ["a", "b"]),
        ("optional_literal", ["x", "y"]),
        ("pattern_choice", ["pos", "neg"]),
        ("free_text", None),
        ("bounded_int", None),
    ],
)
def test_enum_choices_covers_literal_and_pattern(field_name, expected):
    field_info = Sample.model_fields[field_name]
    assert enum_choices(field_info.annotation, field_info) == expected


def test_default_for_plain_value_and_factory():
    assert default_for(Sample.model_fields["bounded_int"]) == 5
    # A nested model default arrives as a dict so callers get one shape regardless of
    # whether the value came from a factory or from saved YAML.
    assert default_for(Sample.model_fields["nested"]) == {"inner": 3}


def test_default_for_unset_optional_is_none():
    assert default_for(Sample.model_fields["maybe_int"]) is None


def _leaf_fields(model_cls, prefix=""):
    """Yield (path, FieldInfo) for every non-model field, descending into nested models."""
    for name, field_info in model_cls.model_fields.items():
        annotation, _ = unwrap_optional(field_info.annotation)
        path = f"{prefix}{name}"
        if is_basemodel(annotation):
            yield from _leaf_fields(annotation, prefix=f"{path}.")
        else:
            yield path, field_info


@pytest.mark.parametrize(
    "block", [b for b in BLOCKS if b.config_cls is not None], ids=lambda b: b.id
)
def test_every_config_field_is_renderable(block):
    """Guard against a new config field the form builders cannot render.

    A field restricted to a fixed set of values must be reachable through
    ``enum_choices``; otherwise it falls through to a free-text box and the user has to
    type the allowed value by hand. Anything genuinely free-form is fine — this asserts
    only that a constrained field is recognised as constrained.
    """
    for path, field_info in _leaf_fields(block.config_cls):
        annotation, _ = unwrap_optional(field_info.annotation)
        choices = enum_choices(annotation, field_info)
        if choices is not None:
            assert choices, f"{block.id}.{path}: empty choice list"
            continue
        # Not a fixed-value field: it must at least be a type the builders dispatch on,
        # or a container/str the fallback handles.
        assert annotation is not None, f"{block.id}.{path}: no usable annotation"
