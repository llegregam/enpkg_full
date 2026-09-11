"""Tests for building a form from a Pydantic config model.

The property that matters is the round trip: whatever ``set_values`` writes into the
widgets, ``get_values`` must read back, and the result must satisfy the model. That is
what makes loading a saved config a plain assignment rather than something needing its
own mechanism.
"""

from typing import Literal, Optional

import pytest
from nicegui import ui
from pydantic import BaseModel, Field

from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline.blocks import BLOCKS
from enpkg.monolith.webui.forms import build_form


class Nested(BaseModel):
    depth: int = 2
    tag: str = "inner"


class Sample(BaseModel):
    flag: bool = True
    count: int = Field(default=5, ge=1, le=10)
    ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    mode: Literal["a", "b"] = "a"
    polarity: str = Field(default="pos", pattern="^(pos|neg)$")
    note: str = "hello"
    maybe: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    nested: Nested = Field(default_factory=Nested)


def _form(model_cls, **kwargs):
    """Build a form inside a throwaway container.

    Widgets must be created inside a NiceGUI slot, so the container is required even
    though nothing is rendered to a client.
    """
    with ui.column():
        return build_form(model_cls, **kwargs)


def test_defaults_round_trip_through_the_model():
    form = _form(Sample)
    assert Sample.model_validate(form.get_values()) == Sample()


def test_set_values_then_get_values_returns_what_was_written():
    form = _form(Sample)
    written = Sample(
        flag=False,
        count=9,
        ratio=0.25,
        mode="b",
        polarity="neg",
        note="changed",
        maybe=4,
        tags=["x", "y"],
        nested=Nested(depth=7, tag="deep"),
    ).model_dump()
    form.set_values(written)
    assert form.get_values() == written


def test_absent_keys_reset_to_defaults_not_to_the_previous_value():
    """Loading is a full replace: a file that omits a setting must not keep the old one."""
    form = _form(Sample)
    form.set_values({"count": 9, "note": "changed"})
    assert form.get_values()["count"] == 9

    form.set_values({"note": "second"})
    assert form.get_values()["count"] == 5
    assert form.get_values()["note"] == "second"


def test_nested_models_reset_too():
    form = _form(Sample)
    form.set_values({"nested": {"depth": 7, "tag": "deep"}})
    assert form.get_values()["nested"]["depth"] == 7

    form.set_values({})
    assert form.get_values()["nested"] == {"depth": 2, "tag": "inner"}


def test_excluded_field_is_not_rendered_and_not_returned():
    form = _form(Sample, exclude_fields={"note"})
    assert "note" not in form.get_values()


def test_excluded_nested_field_uses_a_dotted_path():
    form = _form(Sample, exclude_fields={"nested.tag"})
    assert "tag" not in form.get_values()["nested"]
    assert "depth" in form.get_values()["nested"]


def test_an_out_of_range_choice_falls_back_to_the_default():
    """Quasar shows a value outside the option list as blank, which reads as data loss."""
    form = _form(Sample)
    form.set_values({"mode": "retired_member"})
    assert form.get_values()["mode"] == "a"


def test_an_empty_optional_number_is_none_not_zero():
    """0 is a real value for these fields, so it must not stand in for "unset".

    SerializerConfig.max_ions_per_spectrum is a slice bound: 0 emits no ions at all,
    where None means no cap.
    """
    form = _form(SerializerConfig)
    form.set_values({"max_ions_per_spectrum": None})
    assert form.get_values()["max_ions_per_spectrum"] is None


def test_serializer_config_round_trips():
    form = _form(SerializerConfig)
    written = SerializerConfig(
        top_k_ms1=2, include_ions=True, max_ions_per_spectrum=25
    ).model_dump()
    form.set_values(written)
    assert SerializerConfig.model_validate(form.get_values()).model_dump() == written


@pytest.mark.parametrize(
    "block", [b for b in BLOCKS if b.config_cls is not None], ids=lambda b: b.id
)
def test_every_block_config_can_be_rendered_and_read_back(block):
    """A new config field the builder cannot handle shows up here rather than in the GUI."""
    form = _form(block.config_cls)
    values = form.get_values()
    assert set(values) == set(block.config_cls.model_fields)


def test_on_change_fires_when_a_widget_changes():
    calls = []
    with ui.column():
        form = build_form(Sample, on_change=lambda: calls.append(1))
    form.fields["count"].set(8)
    # set() assigns the widget value; the change handler is what a page uses to know it
    # should revalidate.
    assert form.get_values()["count"] == 8
