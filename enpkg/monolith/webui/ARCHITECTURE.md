# The NiceGUI front end — architecture

How the interface under [enpkg/monolith/webui/](.) is put together: what each module is
responsible for, where a user's choices are kept, and how a pipeline run is started,
watched and stopped.

The reference for NiceGUI itself is [docs/niceGUI_LLM_docs.md](../../../docs/niceGUI_LLM_docs.md),
which ships inside the installed package and therefore matches the API this project has.
Three properties from it decide most of what follows, so they are stated once here:

- **A page function runs once per page load.** `@ui.page` builds the page and is never
  called again to reflect a change. Updates happen by mutating an element, by a binding,
  or by explicitly rebuilding a section.
- **Module-level variables are shared by every connected client**, not per user.
- **There is no virtual DOM.** Rebuilding a section destroys and recreates its elements,
  losing focus, scroll position and which expansions were open — it does not compute a
  difference and patch it.

---

## 1. What this layer may and may not do

The interface imports from `enpkg/monolith/pipeline/`; the reverse is forbidden. That
direction is what keeps the pipeline runnable from the command line with no GUI
dependency installed, and it has been violated once before — the pipeline modules
originally lived under `gui/`, which made the pipeline nominally depend on an optional
dependency group.

Beyond that, this layer holds no orchestration logic of its own. Which blocks exist, what
order they run in, how a config is validated and what a run does are all decided
elsewhere:

| Question | Answered by |
|---|---|
| Which blocks exist, in what order, with what dependencies | `pipeline/blocks.py` (`BLOCKS`) |
| What settings a block takes | the block's `config_cls.model_fields` |
| Whether a configuration is acceptable | Pydantic, via `config_io.build_configs` |
| What a run actually does | the `enpkg` command line, in a separate process |

A page that hard-codes a block id, a field name or a validation rule is a bug: all four
of those are derived.

---

## 2. Module map

```
enpkg/monolith/webui/
├── __init__.py        docstring only — importing enpkg.monolith must not pull in NiceGUI
├── paths.py           workspace directories; the storage signing key
├── state.py           per-visitor state (see §3)
├── runs.py            starting, watching and stopping a run (see §4)
├── forms.py           Pydantic model → widgets (see §5)
├── layout.py          the frame every page is drawn in: header, navigation, run indicator
├── folder_picker.py   choosing a directory, natively or served (see §6)
├── main.py            run_app(): the ui.run() call and its arguments
├── __main__.py        `python -m enpkg.monolith.webui`, with the __main__ guard
└── pages/
    ├── __init__.py    imports the three page modules, which registers their routes
    ├── imports.py     `/` (redirect) and `/imports`
    ├── pipeline.py    `/pipeline`
    └── serializer.py  `/serializer`
```

`state.py`, `runs.py` and `forms.py` contain no page code and are unit-testable without a
server or a browser. `pages/` only assembles.

---

## 3. Where a user's choices live

### 3.1 The problem

Moving between pages is a full page load. The page function runs again from the start and
every element object from the previous page is gone, so nothing a user chose can be kept
in a page function's local variables. The obvious alternative — a module-level dictionary
— is worse, because in NiceGUI that is shared by every connected client.

### 3.2 The split

State is divided by what it is made of, not by what it means.

**`app.storage.user`** holds everything that survives `json.dumps`. It is per visitor and
also survives a browser reload, which `st.session_state` in the Streamlit app did not.
Accessed only through `state.py`, under a single `enpkg` key so nothing collides with
NiceGUI's own use of that store:

| Key | Holds |
|---|---|
| `sid` | this visitor's id — the key into `SESSIONS` below |
| `mode` | `single` or `batch` |
| `input_dir`, `batch_dir`, `config_path` | folder and file paths, **as strings** |
| `spectra`, `metadata`, `quant`, `sirius_spectra` | chosen **filenames**, resolved against `input_dir` when used |
| `selected_blocks` | list of block ids |
| `form_state` | `{block_id: raw values}` |
| `general_params`, `serializer` | the shared and serializer settings |
| `verbose` | whether runs get debug logging |

**`state.SESSIONS`** holds everything that does not: the subprocess handle, its output
buffer, the parsed result. The dictionary is module-level — and therefore shared — but no
value is ever read from it except through the caller's own `sid`, so one visitor cannot
reach another's. With a single visitor, which is what the native window is, it degenerates
to one entry and needs no special case.

**Page-local** is everything else: element handles, `ModelForm` objects, the log element,
the read cursor. Rebuilt on every page load and never escaping the page function.

### 3.3 Two rules that are easy to get wrong

**Nothing that fails `json.dumps` may reach `app.storage.user`.** A `Path` or a dataclass
put there fails when NiceGUI persists it, which can happen long after the page rendered
correctly. `state.py` stores strings and exposes `input_path()`, `batch_path()` and
`config_path()` for callers that want a `Path`. `ExperimentInputs`, which is a dataclass
of paths, is never stored at all — `discover_experiments` is a cheap directory scan and is
simply run again, which also cannot go stale when a subfolder is added.

**Missing values are filled in individually, never by replacing the mapping.**
`state.raw()` uses `setdefault` per key. Replacing the whole mapping when it looks
unfamiliar means a single access from a context holding a different store silently
discards every choice the visitor has made. That was a real defect, caught by a page test.

### 3.4 Session lifetime

Sessions are pruned lazily, only when they hold no run and have been idle for an hour.
They are deliberately **not** cleaned up when a client disconnects: navigating between
pages *is* a disconnect, so doing that would kill a running pipeline on every page change.

---

## 4. Running the pipeline

### 4.1 Why a separate process

A run takes minutes to hours. Executing it inside the server would block the event loop,
give no way to stop it, and lose everything if the browser was closed. So `runs.py` starts
the `enpkg` command line as a separate operating-system process:

```
sys.executable -m enpkg.cli run --config <run_dir>/config.yaml
                                --output-dir <run_dir>
                                --json-out <run_dir>/result.json
```

`sys.executable -m` rather than the `enpkg` console script, so the run uses the same
interpreter and virtual environment as the server with no dependency on what is on `PATH`.

Each run gets its own directory under `gui_workspace/runs/`, holding the config it was
launched with, its logs, its Turtle export and its result file. Two runs therefore cannot
overwrite each other, and the settings that produced an output stay beside that output.
Because the config is written before launching, a run is reproducible from a shell by
copying its `argv`.

### 4.2 The reader touches nothing on screen

This is the decision the rest depends on. `runs._pump` reads the subprocess's output into
`handle.lines` and increments `handle.seq`. It holds no client context and updates no
element.

That is what makes a run survive navigation. When the client that started the run is
destroyed — which is what going to another page does — there is nothing left pointing at a
destroyed element and nothing queued for a client that no longer exists. A page that wants
to display the output *pulls*:

```python
log = ui.log(max_lines=runs.MAX_BUFFERED_LINES)
...
pending, cursor = handle.lines_since(cursor)
for line in pending:
    log.push(line)
```

using a timer the page owns, which NiceGUI cancels when that page is left.

`seq` counts lines *ever* appended, not lines currently buffered. `handle.lines` is a
bounded deque, so once it starts discarding old lines a cursor counted against the buffer
would be wrong; counted against `seq` it cannot be. `ui.log` only appends — it has no
replace operation — so re-pushing the buffer on each poll would duplicate every line
several times a second.

The buffer bounds memory at roughly 200 KB per run. Nothing is lost: the complete log is
on disk and the result file names it.

### 4.3 Three details that each look like a hang

Every one of these produces silence rather than an error, so they are recorded here.

**`stderr` is merged into `stdout`.** The pipeline's console log handler writes to stderr.
Given its own pipe that nobody drains, the run blocks as soon as the operating system's
buffer fills, partway through and with no message.

**`PYTHONUNBUFFERED=1` is set in the child's environment.** Python block-buffers its output
when it is a pipe rather than a terminal. Without it the log panel stays empty for minutes
and then delivers everything at once.

**Cancel terminates the process tree.** SIRIUS runs as a Java subprocess of the pipeline.
Terminating only the direct child leaves it running and holding its output directory, so
the *next* run fails on a directory it cannot write. `psutil` is used for this and is
already a direct dependency.

### 4.4 What comes back

The `Analysis` does not. It lives in the other process's memory and is gone when that
process exits. Everything the interface can display therefore has to be in the result
file, which is written and read through `pipeline/run_artifact.py` — one module shared with
the command line, so the writer and the reader cannot disagree about its shape.

This is a real reduction against the Streamlit app, which read a live `Analysis`. The
artifact is defined as a projection of `RunResult` and `AnalysisSummary` rather than an
ad-hoc structure, so adding something to the results panel means adding it there.

---

## 5. Forms from config models

`forms.build_form(model_cls)` creates one widget per field of a Pydantic model and returns
a `ModelForm`. `get_values()` returns a plain nested dict for `model_validate`;
`set_values(data)` writes every widget.

**Widgets are persistent Python objects.** One is created per field when the page loads and
keeps its identity for that page's lifetime, so loading a saved config is an assignment to
each widget's `value`. The Streamlit app needed a `form_rev` counter and a fresh key
namespace to achieve the same thing, because it identified widgets by string key and
ignored a changed `value=` for a key it had already registered. None of that machinery is
ported; it has no counterpart here.

A model holds two kinds of field. `duckdb_path` is a string and becomes one widget;
`general_params` is itself a model and becomes a nested group inside an expansion. Both
live in `ModelForm.fields`, because a `ModelForm` answers the same `get()`/`set()` pair as
a single field — a nested model's `get()` returns its own dict, which is what the parent
needs to pass on.

Type mapping: `bool` → checkbox; `int`/`float` → number input carrying the field's
`ge`/`le` bounds; a `Literal` or a `^(a|b)$` pattern → dropdown; a long or multi-line
string → text area; other strings → text input; `list`/`tuple` → text area, one value per
line; `Optional[T]` → the widget for `T`. Field descriptions become tooltips.

**Pydantic remains the only validator.** Widget bounds narrow what can be typed; whether a
configuration is acceptable is decided by `model_validate`, and its error is shown
verbatim.

Three behaviours worth knowing:

- **`set_values` writes every field**, not only those the data mentions, filling the rest
  from the model's defaults. A partial write would leave values from a previously loaded
  config in place, so a file that omits a setting would no longer describe the run it
  produces. This is the same full-replace rule that `selected_blocks` exists to make safe.
- **An empty numeric box reads back as `None`, not `0`.** For
  `SerializerConfig.max_ions_per_spectrum` that distinction decides whether every ion is
  emitted or none, since it is used as a slice bound. A required field left empty also
  yields `None`, so Pydantic reports the omission rather than the form inventing a value.
- **A dropdown given a value outside its options falls back to the default.** Quasar
  renders an unknown value as a blank box, which reads as data loss; this happens when an
  older config names a member that has since been retired.

---

## 6. Choosing a folder, in both modes

The server cannot see the machine running the browser, so a served page has no access to
an operating-system file dialog. `folder_picker.py` covers both cases behind one call,
detecting the mode at runtime from `app.native.main_window`:

- **Native** — pywebview's real folder dialog.
- **Served** — a dialog listing directories the *server* can see, which in served mode is
  the machine holding the data.

Both end at the same callback, so pages never branch on the mode. The typed path box is
always live; the button is an accelerator, not the only way in. Directory listing goes
through `run.io_bound`, because a network share can take seconds and doing it on the event
loop would stall every other client.

**`ui.run(host=...)` defaults to loopback.** The served dialog browses the server's own
filesystem, so binding to every interface exposes that to anyone who can reach the port.
Serving to a network should be a deliberate act.

---

## 7. The pages

### 7.1 `/imports`

Chooses the data. Single mode wants three files from one folder; batch mode wants a parent
folder holding shared metadata and a subfolder per experiment, which
`discover_experiments` finds.

File lists come from a background task rather than being built in the page function: a
page builder has a few seconds before the client gives up on it, and scanning a network
share can exceed that. The route also raises its timeout.

Metadata accepts `.tsv`, `.txt` and `.csv` while quantification tables are `.csv`, so the
two overlap. The suffix tuples are **preference orders**, not sets: the first suffix with a
match wins. Treating them as a set means a normal folder resolves its metadata to the quant
table, and the run then fails deep inside the loader looking for a metadata column among
quant columns. That is a defect this project has already had, in the command line.

### 7.2 `/pipeline`

Block checkboxes, the shared `GeneralParams` form, one settings group per block, the
configuration preview, config load and save, and the run controls.

**Every block's form is built at page load** and then shown or hidden by its checkbox,
rather than created when the block is ticked. Loading a config therefore always has a form
to write into, and turning a block off and on again keeps what was typed in it. MS1 and MS2
share one config, so they share one form.

**Nothing is wrapped in a refreshable except the preview.** With no virtual DOM, rebuilding
a section discards expansion state, scroll position and focus, so visibility is toggled
instead of rebuilding.

**Validation runs on a timer**, off a dirty flag, at most once a second. Running Pydantic
over forty fields on every keystroke is wasteful and reports errors for values that are
half-typed.

**Save and Run read the widgets first.** The stored values are written by that same timer,
so acting on them directly would use the values from before the most recent change. This
was a real defect, caught by a page test.

### 7.3 `/serializer`

A form over `SerializerConfig` and little else, saved under the `serializer` key of the
config file. Because it is generated from the model's fields, a new option appears here by
adding a field to that model, and grouping related options means nesting a model, which
renders as its own section.

The page states explicitly that the molecular-network edges are not an option, because that
is the setting a user is most likely to go looking for. Whether those edges are emitted
follows from whether the networking block is part of the run.

---

## 8. Testing

Tests live in [enpkg/tests/test_webui/](../../tests/test_webui/) and use NiceGUI's `user`
fixture, which runs in the same process and needs no browser.

`test_forms.py` and `test_runs.py` are pure logic and are where most of the value is.
`test_runs.py` drives a stand-in subprocess that prints lines and writes a result file,
covering streaming, the read cursor, buffer truncation, failure exit codes and real process
termination — without a database or a multi-minute pipeline.

`test_pages.py` drives the interface as a person would. It has to: the per-visitor store is
only reachable inside a request, so a test cannot seed it from outside, and one that tried
would be asserting on something the page never saw.

Four things about the harness that are not obvious:

- Only `nicegui.testing.user_plugin` is registered, not `nicegui.testing.plugin`. The
  latter also imports the `screen` fixtures, which require Selenium.
- `pytest_plugins` is honoured only in the **root** `conftest.py` under pytest 8.
- The `main_file` setting in `pytest.ini` points at `nicegui_entry.py`, a test-only entry
  script. The harness clears the route table before each test and imports that file to
  rebuild it. Its name avoids both `test_*.py` and `*_test.py`, because a name matching
  either would be collected as a test module and its `ui.run()` executed for real, hanging
  collection.
- A conftest fixture works around a NiceGUI cleanup failure on Windows, written up in
  [docs/NICEGUI_STORAGE_CLEANUP_BUG.md](../../../docs/NICEGUI_STORAGE_CLEANUP_BUG.md).

**Known gap.** No test starts a real run from the page. The run registry is covered against
a stand-in process, and the page is covered up to the point of launching, but the join
between them has only been exercised by hand.

---

## 9. Adding to the interface

- **A new pipeline block** needs nothing here. The checkboxes, the settings group, the
  config section and the run all follow from its `BlockSpec` and its `config_cls`. See
  [docs/ADDING_A_BLOCK.md](../../../docs/ADDING_A_BLOCK.md).
- **A new serializer option** is a field on `SerializerConfig`.
- **A new field type** the form builder cannot render is a branch in `forms._build_field`,
  plus a helper in `configuration/introspect.py` if it needs new reflection. The
  parametrised test over every block config is what catches an unrenderable field.
- **A new page** is a module under `pages/`, imported by `pages/__init__.py`, drawn inside
  `layout.page_frame`, and added to `layout.PAGES`.
