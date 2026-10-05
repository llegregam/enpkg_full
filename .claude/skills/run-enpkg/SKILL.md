---
name: run-enpkg
description: Start, run, drive and screenshot the enpkg NiceGUI web interface, and smoke-test the enpkg command line. Use when asked to run or start the enpkg GUI, take a screenshot of the Imports, Pipeline or Serializer page, click through a batch or single run from the GUI, check a GUI change in the real app, run `enpkg batch`/`enpkg run` on sample data, or run the test suite.
---

enpkg has a command line (`poetry run enpkg …`) and a NiceGUI web interface. Agents drive
the interface with `.claude/skills/run-enpkg/driver.py`. It starts the server from the
Poetry environment, reads one command per line from stdin, drives the installed Google
Chrome headlessly through Playwright, and stops the server when the script ends.
`chromium-cli` and Node are not installed on this machine; the driver replaces them.

All paths are relative to the repository root. Everything below was run on Windows 11 in
Git Bash.

## Prerequisites

- The Poetry environment with the optional `webui` group, which holds NiceGUI. This
  command was checked with `--dry-run` only, on an environment that already had it:

  ```bash
  poetry install --with webui
  ```

- `uv` on `PATH`. The driver runs in a throwaway environment that `uv` builds and
  caches, so Playwright is never installed into the project.
- Google Chrome installed. The driver launches it with `channel="chrome"`, so no
  `playwright install` download is needed.

## Sample data

The real batch folders under `gui_workspace/` are local and git-ignored. Build a
two-experiment batch folder from the tracked test data instead:

```bash
mkdir -p gui_workspace/batch_demo
cp enpkg/monolith/test-data/qualome_metadata.txt gui_workspace/batch_demo/
for e in actea_EtOAc-1_pos actea_EtOAc-2_pos; do
  mkdir -p gui_workspace/batch_demo/$e
  cp enpkg/monolith/test-data/$e.mgf enpkg/monolith/test-data/${e}_quant.csv gui_workspace/batch_demo/$e/
done
```

## Run (agent path)

Tick one of the two experiments, run the networking block on it from the Pipeline page,
and wait for the run to end. Takes about a minute.

```bash
uv run --no-project --with playwright python .claude/skills/run-enpkg/driver.py <<'EOF'
nav /imports
click Batch
fill Parent folder = gui_workspace/batch_demo
wait 2 of 2 experiments selected
untick actea_EtOAc-2_pos
wait 1 of 2 experiments selected
shot imports
nav /pipeline
click Molecular networking
wait Configuration is valid
click Run
timeout 900
wait-run
shot pipeline_done
errors
EOF
```

Screenshots and the server log land in `gui_workspace/shots/`. Each GUI run writes
`gui_workspace/runs/<run|batch>_<id>/`, holding `config.yaml`, `experiments.txt` when a
subset was ticked, `result.json`, and the pipeline outputs. Read the screenshots: a
`wait` passing does not prove the page looks right.

| command | what it does |
|---|---|
| `nav <path>` | open a page, e.g. `/imports`, `/pipeline`, `/serializer` |
| `click <text>` | click the button named `<text>`, else the element showing `<text>` |
| `fill <label> = <value>` | type into the input with that label |
| `tick <row>` / `untick <row>` | set the checkbox of the table row containing `<row>` |
| `wait <text>` | wait until `<text>` is visible; `A\|B` waits for either |
| `wait-run` | wait until a started run ends, and print its status line |
| `timeout <seconds>` | timeout for later waits; default 20 |
| `sleep <ms>` | pause |
| `shot <name>` | full-page screenshot to `gui_workspace/shots/<name>.png` |
| `text` | print the page's visible text |
| `errors` | print browser console errors |

Options: `--port` (default 8099, leaving 8080 free for your own instance), `--out` for the
screenshot folder, and `--no-serve` to drive a server that is already running on `--port`.

## Command line

```bash
poetry run enpkg batch discover --parent-dir gui_workspace/batch_demo
mkdir -p gui_workspace/cli_smoke
poetry run enpkg config init --blocks network --out gui_workspace/cli_smoke/config.yaml --force
printf 'actea_EtOAc-1_pos\n' > gui_workspace/cli_smoke/chosen.txt
poetry run enpkg batch --config gui_workspace/cli_smoke/config.yaml --parent-dir gui_workspace/batch_demo --output-dir gui_workspace/cli_smoke --experiments-file gui_workspace/cli_smoke/chosen.txt > gui_workspace/cli_smoke/batch.out 2>&1; echo "exit=$?"; tail -4 gui_workspace/cli_smoke/batch.out
```

Expect `Batch finished: 1 ok, 0 failed` and `exit=0`.

## Run (human path)

`poetry run enpkg gui` serves the interface on port 8080 and opens a browser tab;
`--native` opens a desktop window instead. Neither is usable headlessly.

## Test

```bash
poetry run pytest enpkg/tests/test_webui enpkg/tests/test_cli enpkg/tests/test_pipeline -q
```

All pass. One `RuntimeWarning: coroutine 'Outbox.loop' was never awaited` is expected; it
comes from NiceGUI's test harness and also appears on unchanged code.

## Gotchas

- **`wait Finished` returns while the run is still going.** The log panel shows
  `=== [<run name>] Finished ===` after each experiment of a batch. Use `wait-run`, which
  matches only the status line that starts with `Finished —`, `Failed —` or `Cancelled`.
- **Ending the script mid-run loses the result file.** The driver stops the server, but on
  Windows the run it launched keeps going. Observed: the detached run wrote every
  experiment's outputs, then exited without writing `result.json`. Always `wait-run` before
  the script ends.
- **The header badge lags.** It can still read "Running…" after the Execution card shows
  the run ended. Trust the status line in the Execution card.
- **Every driver launch is a new visitor.** Stored choices start from the defaults: Single
  experiment mode and no blocks ticked. Scripts must `click Batch` and tick blocks each time.
- **The batch table is hidden until `click Batch`.** Waits on its rows time out before then.
- **Relative paths typed into the page resolve against the server's working directory**,
  which the driver sets to the repository root.
- **Full-page screenshots repeat the fixed header** partway down the image. Cosmetic.
- **`enpkg … | tail` reports `tail`'s exit code, not `enpkg`'s.** Redirect to a file and
  echo `$?`, as in the command-line block above.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'playwright'`**: the driver was run with the
  Poetry interpreter. Run it through `uv run --no-project --with playwright` as shown.
