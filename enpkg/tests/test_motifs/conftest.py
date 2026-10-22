"""Fixtures for the Mass2Motif tests."""

from pathlib import Path

import pytest

from enpkg.tests.test_motifs.model_folders import write_model_folder


@pytest.fixture
def model_folder(tmp_path) -> Path:
    """A completed model folder with the default contents of `model_folders`."""
    return write_model_folder(tmp_path / "model")
