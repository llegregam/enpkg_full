"""Tests for the RDF serializer's configuration model.

``SerializerConfig`` is passed to the serializer as ``AnalysisSerializer(**dump)``, so
its field names and defaults must track the constructor exactly. These tests pin that
correspondence rather than trusting the two to be edited together.
"""

import inspect

import pytest
from pydantic import ValidationError

from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.rdf.serializer import AnalysisSerializer


def _constructor_params():
    """Return the serializer's keyword-only parameters, minus ``self``."""
    params = inspect.signature(AnalysisSerializer.__init__).parameters
    return {name: p for name, p in params.items() if name != "self"}


def test_field_names_match_the_constructor_exactly():
    """``AnalysisSerializer(**config.model_dump())`` depends on this correspondence."""
    assert set(SerializerConfig.model_fields) == set(_constructor_params())


@pytest.mark.parametrize("name", sorted(SerializerConfig.model_fields))
def test_defaults_match_the_constructor(name):
    """A default that drifts would silently change every graph the pipeline writes."""
    assert getattr(SerializerConfig(), name) == _constructor_params()[name].default


def test_dump_is_accepted_by_the_serializer():
    serializer = AnalysisSerializer(**SerializerConfig().model_dump())
    assert serializer.include_fbmn_components is True
    assert serializer.include_ions is False


def test_max_ions_per_spectrum_rejects_zero():
    """Zero would emit no ions at all, unlike the top_k fields where it means "all".

    The serializer stores this one raw and slices with it, so a 0 arriving from a form
    or a hand-written YAML would produce an empty ion set while writing
    ``enpkg:maxIonsPerSpectrum 0`` as provenance -- an empty result that reads as
    deliberate. Rejecting it at the config boundary is what keeps that from happening.
    """
    with pytest.raises(ValidationError):
        SerializerConfig(max_ions_per_spectrum=0)
    with pytest.raises(ValidationError):
        SerializerConfig(max_ions_per_spectrum=-1)


def test_max_ions_per_spectrum_allows_unset_and_positive():
    assert SerializerConfig().max_ions_per_spectrum is None
    assert SerializerConfig(max_ions_per_spectrum=50).max_ions_per_spectrum == 50


@pytest.mark.parametrize("field", ["top_k_ms1", "top_k_ms2", "top_k_sirius"])
def test_top_k_accepts_non_positive_meaning_no_cap(field):
    """The serializer normalises <=0 to None, so the config must let it through."""
    cfg = SerializerConfig(**{field: 0})
    serializer = AnalysisSerializer(**cfg.model_dump())
    assert getattr(serializer, field) is None


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_min_relative_intensity_is_bounded(value):
    """It multiplies the base peak, so anything outside 0-1 is a mistake."""
    with pytest.raises(ValidationError):
        SerializerConfig(min_relative_intensity=value)


def test_min_relative_intensity_accepts_the_bounds():
    assert SerializerConfig(min_relative_intensity=0.0).min_relative_intensity == 0.0
    assert SerializerConfig(min_relative_intensity=1.0).min_relative_intensity == 1.0
