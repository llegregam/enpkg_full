"""Tests for starting, watching and cancelling a run.

A real pipeline run takes minutes and needs a database, so these drive a stand-in process
that prints a few lines and writes a result artifact. That is enough to exercise
everything the front end relies on: output arriving while the process is alive, the read
cursor, the final status, the parsed artifact, and termination.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from enpkg.monolith.pipeline.run_artifact import SCHEMA_VERSION
from enpkg.monolith.webui import runs
from enpkg.monolith.webui.runs import MAX_BUFFERED_LINES, RunHandle

_ARTIFACT = {
    "schema_version": SCHEMA_VERSION,
    "kind": "run",
    "status": "ok",
    "error": None,
    "executed": ["network"],
    "skipped": [],
    "durations": {"network": 1.0},
    "summary": {
        "run_name": "FAKE",
        "n_spectra": 3,
        "n_ott_matches": 0,
        "n_network_nodes": 3,
    },
    "log_file": None,
    "summary_file": None,
    "ttl_file": None,
}


@pytest.fixture(autouse=True)
async def _nicegui_loop():
    """Supply the event loop NiceGUI normally records when the server starts.

    ``background_tasks.create`` reads it to attach the task reference that keeps a
    long-running coroutine from being garbage-collected mid-run. Under pytest nothing has
    started a server, so it has to be set here.
    """
    from nicegui import core

    previous = core.loop
    core.loop = asyncio.get_running_loop()
    yield
    core.loop = previous


def _fake_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_run.py"
    script.write_text(body, encoding="utf-8")
    return script


def _handle(tmp_path: Path, script: Path, *args: str) -> RunHandle:
    handle = runs.new_handle("run", tmp_path / "runs")
    handle.argv = [sys.executable, str(script), str(handle.result_path), *args]
    return handle


async def _wait_until_settled(handle: RunHandle, timeout: float = 10.0) -> None:
    """Wait for the pump to finish, rather than sleeping a fixed amount."""
    deadline = asyncio.get_running_loop().time() + timeout
    while handle.is_active:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"run did not settle; status={handle.status}")
        await asyncio.sleep(0.02)


_PRINTS_AND_SUCCEEDS = """
import json, sys, time
result_path = sys.argv[1]
for i in range(5):
    print(f"line {i}", flush=True)
    time.sleep(0.01)
json.dump(json.loads(sys.argv[2]), open(result_path, "w"))
"""

_FAILS = """
import sys
print("about to fail", flush=True)
sys.exit(3)
"""

_RUNS_FOREVER = """
import time
print("started", flush=True)
while True:
    time.sleep(0.05)
"""


async def test_output_is_buffered_and_status_becomes_done(tmp_path):
    script = _fake_script(tmp_path, _PRINTS_AND_SUCCEEDS)
    handle = _handle(tmp_path, script, json.dumps(_ARTIFACT))

    await runs.start(handle)
    await _wait_until_settled(handle)

    assert handle.status == "done"
    assert handle.returncode == 0
    assert list(handle.lines) == [f"line {i}" for i in range(5)]
    assert handle.seq == 5


async def test_the_result_artifact_is_read_back(tmp_path):
    script = _fake_script(tmp_path, _PRINTS_AND_SUCCEEDS)
    handle = _handle(tmp_path, script, json.dumps(_ARTIFACT))

    await runs.start(handle)
    await _wait_until_settled(handle)

    assert handle.result is not None
    assert handle.result["summary"]["n_spectra"] == 3
    assert handle.result["executed"] == ["network"]


async def test_lines_since_returns_only_what_is_new(tmp_path):
    """A page records a position and asks for what arrived after it.

    Re-sending the whole buffer on every poll would duplicate every line, since the log
    element only ever appends.
    """
    script = _fake_script(tmp_path, _PRINTS_AND_SUCCEEDS)
    handle = _handle(tmp_path, script, json.dumps(_ARTIFACT))

    await runs.start(handle)
    await _wait_until_settled(handle)

    first, cursor = handle.lines_since(0)
    assert first == [f"line {i}" for i in range(5)]

    second, cursor = handle.lines_since(cursor)
    assert second == []
    assert cursor == handle.seq


def test_lines_since_survives_a_truncated_buffer():
    """seq counts lines ever seen, so a dropped line cannot strand the cursor."""
    handle = RunHandle(
        id="x", kind="run", argv=[], run_dir=Path("."),
        config_path=Path("c"), result_path=Path("r"),
    )
    for i in range(MAX_BUFFERED_LINES + 50):
        handle.lines.append(f"line {i}")
        handle.seq += 1

    pending, cursor = handle.lines_since(0)
    assert len(pending) == MAX_BUFFERED_LINES
    assert cursor == MAX_BUFFERED_LINES + 50
    assert handle.lines_since(cursor) == ([], cursor)


async def test_a_failing_run_reports_failed_and_keeps_its_output(tmp_path):
    script = _fake_script(tmp_path, _FAILS)
    handle = _handle(tmp_path, script)

    await runs.start(handle)
    await _wait_until_settled(handle)

    assert handle.status == "failed"
    assert handle.returncode == 3
    assert "about to fail" in "\n".join(handle.lines)
    assert handle.error is not None


async def test_a_run_with_no_artifact_still_settles(tmp_path):
    """The stand-in writes nothing here, as a crashed run would."""
    script = _fake_script(tmp_path, _FAILS)
    handle = _handle(tmp_path, script)

    await runs.start(handle)
    await _wait_until_settled(handle)

    assert handle.result is None
    assert handle.status == "failed"


async def test_cancel_stops_the_process(tmp_path):
    script = _fake_script(tmp_path, _RUNS_FOREVER)
    handle = _handle(tmp_path, script)

    await runs.start(handle)
    while handle.seq == 0:
        await asyncio.sleep(0.02)

    await runs.cancel(handle)
    await _wait_until_settled(handle)

    assert handle.status == "cancelled"
    assert handle.result is None


async def test_cancelling_an_already_finished_run_is_harmless(tmp_path):
    script = _fake_script(tmp_path, _PRINTS_AND_SUCCEEDS)
    handle = _handle(tmp_path, script, json.dumps(_ARTIFACT))

    await runs.start(handle)
    await _wait_until_settled(handle)

    await runs.cancel(handle)
    assert handle.status == "done"


def test_build_argv_uses_the_running_interpreter(tmp_path):
    """Not the `enpkg` console script: the run must use this virtual environment."""
    handle = runs.new_handle("run", tmp_path / "runs")
    argv = runs.build_argv(
        "run",
        {"config": handle.config_path, "run_dir": handle.run_dir, "result": handle.result_path},
        input_dir=tmp_path / "data",
        verbose=True,
    )
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "enpkg.cli", "run"]
    assert "--json-out" in argv and str(handle.result_path) in argv
    assert "--output-dir" in argv and str(handle.run_dir) in argv
    assert "--verbose" in argv


def test_build_argv_for_a_batch_passes_the_parent_folder(tmp_path):
    handle = runs.new_handle("batch", tmp_path / "runs")
    argv = runs.build_argv(
        "batch",
        {"config": handle.config_path, "run_dir": handle.run_dir, "result": handle.result_path},
        parent_dir=tmp_path / "experiments",
    )
    assert argv[3] == "batch"
    assert "--parent-dir" in argv
    assert "--input-dir" not in argv


def test_each_run_gets_its_own_directory(tmp_path):
    """Two runs must not overwrite each other's logs or artifact."""
    first = runs.new_handle("run", tmp_path / "runs")
    second = runs.new_handle("run", tmp_path / "runs")
    assert first.run_dir != second.run_dir
    assert first.run_dir.is_dir() and second.run_dir.is_dir()


def test_write_config_puts_the_config_next_to_the_output(tmp_path):
    handle = runs.new_handle("run", tmp_path / "runs")
    runs.write_config(handle, {"selected_blocks": ["network"]})
    assert handle.config_path.is_file()
    assert "network" in handle.config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("running", "Running"),
        ("cancelling", "Stopping"),
        ("cancelled", "Cancelled"),
    ],
)
def test_summary_line_describes_the_status(status, expected):
    handle = RunHandle(
        id="x", kind="run", argv=[], run_dir=Path("."),
        config_path=Path("c"), result_path=Path("r"), status=status,
    )
    assert expected in runs.summary_line(handle)
