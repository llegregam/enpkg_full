"""Tests for choosing a directory.

The starting directory handed to a dialog has to be absolute. A dialog runs in the
operating system's shell, which has no notion of the working directory this process was
started from; on Windows the native folder dialog does not fail on a relative path, it
never returns, so the Browse button appears to do nothing at all. The application's own
defaults are relative, so this is the normal case rather than an edge one.
"""

from pathlib import Path

import pytest

from enpkg.monolith.webui.folder_picker import (
    dialog_start_directory,
    list_subdirectories,
    roots,
    validate_directory,
)


def test_a_relative_directory_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace" / "input").mkdir(parents=True)

    result = dialog_start_directory("workspace/input")

    assert Path(result).is_absolute()
    assert Path(result) == (tmp_path / "workspace" / "input").resolve()


def test_an_absolute_directory_is_kept(tmp_path):
    result = dialog_start_directory(str(tmp_path))
    assert Path(result) == tmp_path.resolve()


@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_value_falls_back_to_home(value):
    assert Path(dialog_start_directory(value)) == Path.home().resolve()


def test_a_missing_directory_falls_back_to_home(tmp_path):
    assert Path(dialog_start_directory(str(tmp_path / "absent"))) == Path.home().resolve()


def test_a_file_is_not_treated_as_a_directory(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    assert Path(dialog_start_directory(str(target))) == Path.home().resolve()


def test_the_result_is_always_an_existing_directory(tmp_path):
    for value in ["", str(tmp_path), "nope/nope", str(tmp_path / "gone")]:
        assert Path(dialog_start_directory(value)).is_dir()


def test_list_subdirectories_returns_only_directories(tmp_path):
    # Its own subdirectory: the workspace fixture populates tmp_path itself.
    root = tmp_path / "listing"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "file.txt").write_text("x", encoding="utf-8")

    names = [p.name for p in list_subdirectories(root)]
    assert names == ["a", "b"]


def test_list_subdirectories_tolerates_an_unreadable_path(tmp_path):
    """An unreadable folder must not stop the dialog rendering."""
    assert list_subdirectories(tmp_path / "absent") == []


def test_roots_are_existing_directories():
    found = roots()
    assert found
    assert all(p.is_dir() for p in found)


def test_validate_directory_accepts_a_directory(tmp_path):
    assert validate_directory(tmp_path) is None


def test_validate_directory_rejects_a_missing_path(tmp_path):
    assert "Does not exist" in validate_directory(tmp_path / "absent")


def test_validate_directory_rejects_a_file(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    assert "Not a directory" in validate_directory(target)
