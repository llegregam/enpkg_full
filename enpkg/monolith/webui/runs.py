"""Starting, watching and cancelling a pipeline run.

A run executes as a separate operating-system process running the ``enpkg`` command line,
not inside the server. That buys three things the in-process alternative cannot give:
output appears while the run is still going, the run can be stopped, and it survives the
browser being reloaded or closed, because the process belongs to the server rather than
to any client.

The part that makes this work is that :func:`_pump` touches no user-interface element.
It reads the subprocess's output into a buffer and nothing else. A page that wants to
display the output reads that buffer with a timer it owns, so when the client that
started the run goes away — which is what navigating to another page does — there is
nothing left pointing at a destroyed element.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import psutil
from nicegui import background_tasks

from enpkg.monolith.pipeline.run_artifact import read_artifact

RunStatus = Literal["starting", "running", "cancelling", "cancelled", "failed", "done"]

# How many output lines are kept in memory per run. The complete log is on disk — the
# artifact names the file — so this only bounds what the page can scroll back through.
MAX_BUFFERED_LINES = 2000


@dataclass
class RunHandle:
    """One pipeline run and everything known about it so far."""

    id: str
    kind: Literal["run", "batch"]
    argv: list[str]
    run_dir: Path
    config_path: Path
    result_path: Path
    proc: Optional[asyncio.subprocess.Process] = None
    lines: deque[str] = field(
        default_factory=lambda: deque(maxlen=MAX_BUFFERED_LINES)
    )
    # Read from all lines ever appended, nopt buffer. A page records this as its read
    # position; counting the buffer instead would make the position wrong as soon as the
    # buffer started discarding old lines.
    seq: int = 0
    status: RunStatus = "starting"
    returncode: Optional[int] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.status in ("starting", "running", "cancelling")

    def lines_since(self, cursor: int) -> tuple[list[str], int]:
        """Return the lines appended after ``cursor``, and the new position.

        When more lines were dropped from the buffer than it can hold, the caller
        silently receives only what is still buffered; the position returned is always
        the current one, so it cannot fall permanently behind.
        """
        pending = self.seq - cursor
        if pending <= 0:
            return [], self.seq
        buffered = list(self.lines)
        return buffered[-min(pending, len(buffered)):], self.seq


def build_argv(
    handle: RunHandle,
    *,
    input_dir: Optional[Path] = None,
    spectra: Optional[Path] = None,
    metadata: Optional[Path] = None,
    quant: Optional[Path] = None,
    sirius_spectra: Optional[Path] = None,
    parent_dir: Optional[Path] = None,
    verbose: bool = False,
) -> list[str]:
    """Build the command line for a run.

    Uses ``sys.executable -m enpkg.cli`` rather than the ``enpkg`` console script, so the
    subprocess runs under the same interpreter and virtual environment as the server with
    no dependency on what is on PATH.
    """
    kind = handle.kind
    argv = [
        sys.executable,
        "-m",
        "enpkg.cli",
        kind,
        "--config",
        str(handle.config_path),
        "--output-dir",
        str(handle.run_dir),
        "--json-out",
        str(handle.result_path),
    ]
    if kind == "run":
        if input_dir is not None:
            argv += ["--input-dir", str(input_dir)]
        for flag, value in (
            ("--spectra", spectra),
            ("--metadata", metadata),
            ("--quant", quant),
            ("--sirius-spectra", sirius_spectra),
        ):
            if value is not None:
                argv += [flag, str(value)]
    else:
        argv += ["--parent-dir", str(parent_dir)]
    if verbose:
        argv.append("--verbose")
    return argv


def new_handle(kind: Literal["run", "batch"], runs_dir: Path) -> RunHandle:
    """Create a handle and its own output directory."""
    run_id = uuid.uuid4().hex[:12]
    run_dir = (runs_dir / f"{kind}_{run_id}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunHandle(
        id=run_id,
        kind=kind,
        argv=[],
        run_dir=run_dir,
        config_path=run_dir / "config.yaml",
        result_path=run_dir / "result.json",
    )


async def start(handle: RunHandle) -> None:
    """Spawn the run and begin reading its output."""
    handle.proc = await asyncio.create_subprocess_exec(
        *handle.argv,
        stdout=asyncio.subprocess.PIPE,
        # The pipeline's console handler writes to stderr. Given its own pipe, nobody
        # would drain it, and the run would block once the operating system's buffer
        # filled — indistinguishable from a hang. Merging also keeps the two streams in
        # the order they were written.
        stderr=asyncio.subprocess.STDOUT,
        # Python block-buffers its output when it is a pipe rather than a terminal, so
        # without this nothing appears until the run finishes.
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=str(Path.cwd()),
    )
    handle.status = "running"
    background_tasks.create(_pump(handle), name=f"enpkg-run-{handle.id}")


async def _pump(handle: RunHandle) -> None:
    """Read the subprocess's output into the buffer until it exits."""
    assert handle.proc is not None and handle.proc.stdout is not None
    try:
        while True:
            raw = await handle.proc.stdout.readline()
            if not raw:
                break
            handle.lines.append(raw.decode("utf-8", "replace").rstrip())
            handle.seq += 1
        handle.returncode = await handle.proc.wait()
    except Exception as exc:  # noqa: BLE001 - surfaced on the page, never raised here
        handle.error = f"Reading the run's output failed: {exc}"
        handle.status = "failed"
        return

    _finish(handle)


def _finish(handle: RunHandle) -> None:
    """Set the final status and read the result artifact."""
    if handle.status == "cancelling":
        handle.status = "cancelled"
        return

    if handle.result_path.is_file():
        try:
            handle.result = read_artifact(handle.result_path)
        except Exception as exc:  # noqa: BLE001
            handle.error = f"Could not read the result file: {exc}"

    if handle.returncode == 0:
        handle.status = "done"
    else:
        handle.status = "failed"
        if handle.error is None:
            handle.error = _error_from(handle)


def _error_from(handle: RunHandle) -> str:
    """Describe a failure, preferring what the run itself reported."""
    if handle.result and handle.result.get("error"):
        return str(handle.result["error"])
    tail = "\n".join(list(handle.lines)[-5:])
    return f"Run exited with code {handle.returncode}." + (f"\n{tail}" if tail else "")


async def cancel(handle: RunHandle) -> None:
    """Stop a running pipeline.

    Terminates the whole process tree, not just the direct child: SIRIUS runs as a Java
    subprocess of the pipeline, and leaving it alive keeps its output directory locked,
    so the next run fails on a directory it cannot write.
    """
    if handle.proc is None or not handle.is_active:
        return
    handle.status = "cancelling"
    try:
        parent = psutil.Process(handle.proc.pid)
        for child in parent.children(recursive=True):
            _terminate(child)
        _terminate(parent)
    except psutil.NoSuchProcess:
        pass


def _terminate(process: psutil.Process) -> None:
    try:
        process.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


async def shutdown_all() -> None:
    """Terminate every live run. Registered as the application's shutdown hook."""
    from enpkg.monolith.webui.state import SESSIONS

    for objects in list(SESSIONS.values()):
        if objects.run is not None and objects.run.is_active:
            await cancel(objects.run)


def write_config(handle: RunHandle, document: dict[str, Any]) -> None:
    """Write the config the run will be launched with into its own directory.

    The run is then reproducible from a shell by copying its ``argv``, and the settings
    that produced a given output stay next to that output.
    """
    import yaml

    handle.config_path.parent.mkdir(parents=True, exist_ok=True)
    handle.config_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )


def summary_line(handle: RunHandle) -> str:
    """Return a one-line description of where a run has got to."""
    if handle.status == "done":
        executed = (handle.result or {}).get("executed") or []
        return f"Finished — executed {', '.join(executed) or 'nothing'}"
    if handle.status == "failed":
        return f"Failed — {handle.error or 'see the log'}"
    if handle.status == "cancelled":
        return "Cancelled"
    if handle.status == "cancelling":
        return "Stopping…"
    return "Running…"


