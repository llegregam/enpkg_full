"""Drive the enpkg NiceGUI interface in headless Chrome.

Starts the web UI from the project's Poetry environment, runs a script of commands read
from stdin against it, and stops the server on exit. Run from the repository root:

    uv run --no-project --with playwright python .claude/skills/run-enpkg/driver.py <<'EOF'
    nav /imports
    wait Input data
    shot imports
    EOF

Commands, one per line (blank lines and lines starting with # are ignored):

    nav <path>              open BASE + path, e.g. /imports
    click <text>            click the button named <text>, else the element showing <text>
    fill <label> = <value>  type <value> into the input labelled <label>
    tick <row text>         tick the table row containing <row text> (no-op if ticked)
    untick <row text>       untick it (no-op if unticked)
    wait <text>             wait until <text> is visible; "A|B" waits for either
    wait-run                wait until a started run ends, then print its status line
    timeout <seconds>       timeout for later waits (default 20)
    sleep <ms>              pause
    shot <name>             full-page screenshot to <out>/<name>.png
    text                    print the page's visible text
    errors                  print browser console errors seen so far

Every browser launch is a new visitor, so per-visitor state (mode, folders, ticked blocks)
starts from the defaults each time.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[3]


def poetry_python() -> str:
    out = subprocess.run(
        ["poetry", "env", "info", "--executable"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    """Start the web UI directly (not via `enpkg gui`, which forks a grandchild)."""
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [poetry_python(), "-m", "enpkg.monolith.webui", "--port", str(port), "--no-show"],
        cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/imports"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"server exited with code {proc.returncode}; see {log_path}")
        try:
            urllib.request.urlopen(url, timeout=2)
            print(f"server up on port {port} (log: {log_path})")
            return proc
        except OSError:
            time.sleep(1)
    proc.terminate()
    sys.exit(f"server did not answer on {url} within 90 s; see {log_path}")


# The status line the page shows once a run has ended (runs.summary_line). Anchored,
# because the run's log also contains "=== [name] Finished ===" lines while it is running.
_RUN_ENDED = re.compile(r"^(Finished —|Failed —|Cancelled$)")


def _text_matcher(text: str):
    return re.compile("|".join(map(re.escape, text.split("|")))) if "|" in text else text


def _set_row(page, row_text: str, ticked: bool) -> None:
    box = page.locator("tr", has_text=row_text).first.locator(".q-checkbox")
    if (box.get_attribute("aria-checked") == "true") != ticked:
        box.click()


def run_script(page, lines, base: str, out: Path, errors: list[str]) -> None:
    timeout_ms = 20_000
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        print(f"> {line}", flush=True)
        if cmd == "nav":
            page.goto(base + arg)
        elif cmd == "click":
            button = page.get_by_role("button", name=arg, exact=True)
            target = button if button.count() else page.get_by_text(arg, exact=True)
            target.first.click()
        elif cmd == "fill":
            label, _, value = arg.partition(" = ")
            page.get_by_label(label.strip()).fill(value.strip())
        elif cmd in ("tick", "untick"):
            _set_row(page, arg, cmd == "tick")
        elif cmd == "wait":
            expect(page.get_by_text(_text_matcher(arg)).first).to_be_visible(timeout=timeout_ms)
        elif cmd == "wait-run":
            status = page.get_by_text(_RUN_ENDED).first
            expect(status).to_be_visible(timeout=timeout_ms)
            print(f"  run ended: {status.inner_text()}")
        elif cmd == "timeout":
            timeout_ms = int(float(arg) * 1000)
        elif cmd == "sleep":
            page.wait_for_timeout(int(arg))
        elif cmd == "shot":
            path = out / f"{arg}.png"
            page.screenshot(path=str(path), full_page=True)
            print(f"  saved {path}")
        elif cmd == "text":
            print(page.locator("body").inner_text())
        elif cmd == "errors":
            print(f"  console errors: {errors or 'none'}")
        else:
            sys.exit(f"unknown command: {cmd}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--no-serve", action="store_true", help="use a server already running")
    parser.add_argument("--out", type=Path, default=REPO / "gui_workspace" / "shots")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    server = None if args.no_serve else start_server(args.port, args.out / "server.log")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1300, "height": 1100})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            run_script(page, sys.stdin, f"http://127.0.0.1:{args.port}", args.out, errors)
            browser.close()
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=30)
            print("server stopped")


if __name__ == "__main__":
    main()
