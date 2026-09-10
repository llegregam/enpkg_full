"""Tests for where a run writes its logs and exports.

``LOG_DIR`` is a module-level *relative* path, so it resolves against whatever directory
the process was started in. That is acceptable for a GUI launched from the repository
root, but not for a command line that can be invoked from anywhere — a run would silently
scatter its logs. Both entry points therefore accept an explicit ``output_dir``.
"""

from pathlib import Path

from enpkg.monolith.pipeline.batch_runner import _make_batch_paths
from enpkg.monolith.pipeline.runner import LOG_DIR, _make_log_paths, run_stamp


def test_log_paths_land_in_the_given_directory(tmp_path):
    log_file, summary_file = _make_log_paths(tmp_path)
    assert log_file.parent == tmp_path
    assert summary_file.parent == tmp_path
    assert tmp_path.is_dir()


def test_log_paths_share_one_stamp(tmp_path):
    """The pair must be visually associable in a directory listing."""
    log_file, summary_file = _make_log_paths(tmp_path)
    assert log_file.name.removeprefix("run_") == summary_file.name.removeprefix("summary_")


def test_log_paths_fall_back_to_the_module_default():
    log_file, _ = _make_log_paths()
    assert log_file.parent == LOG_DIR


def test_batch_paths_land_in_the_given_directory(tmp_path):
    batch_dir, summary_log = _make_batch_paths(tmp_path)
    assert batch_dir.parent == tmp_path
    assert batch_dir.is_dir()
    assert summary_log.parent == batch_dir


def test_nothing_is_written_outside_the_given_directory(tmp_path):
    """The point of the parameter: an explicit output_dir fully contains the run."""
    before = sorted(LOG_DIR.glob("*")) if LOG_DIR.exists() else []
    _make_log_paths(tmp_path / "run")
    _make_batch_paths(tmp_path / "batch")
    after = sorted(LOG_DIR.glob("*")) if LOG_DIR.exists() else []
    assert before == after


def test_run_stamp_is_unique_within_a_second():
    """A one-second-granular stamp alone would let two runs overwrite each other."""
    stamps = {run_stamp() for _ in range(50)}
    assert len(stamps) == 50


def test_run_stamp_sorts_chronologically():
    """The random suffix must not disturb ordering by the time component."""
    stamp = run_stamp()
    date_part, time_part, suffix = stamp.split("_")
    assert len(date_part) == 8 and date_part.isdigit()
    assert len(time_part) == 6 and time_part.isdigit()
    assert len(suffix) == 4


def test_stamped_names_are_filename_safe(tmp_path):
    """Guards against a stamp format that Windows would reject in a filename."""
    log_file, _ = _make_log_paths(tmp_path)
    log_file.write_text("ok", encoding="utf-8")
    assert Path(log_file).read_text(encoding="utf-8") == "ok"
