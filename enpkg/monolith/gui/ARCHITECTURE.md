# ENPKG GUI — Architecture

This document explains how the Streamlit GUI under [enpkg/monolith/gui/](.) is
wired together: what each module is responsible for, how data flows from the
user's clicks into a running pipeline, and the small set of conventions that
keep the design from duplicating knowledge that already lives in the Pydantic
configs and pipeline steps.

## 1. Goals

The GUI is a thin shell around the existing pipeline. It must not:

- re-declare any configuration schema (all validation stays in Pydantic),
- hard-code the list of pipeline blocks (one registry, [blocks.py](blocks.py)),
- duplicate orchestration logic (it all lives in [runner.py](runner.py)).

Everything the user sees — the block checkboxes, the tabs, the form fields,
the validated JSON preview, the runner — is derived from three sources of
truth:

1. `BLOCKS` in [blocks.py](blocks.py) — which pipeline blocks exist, and for
   each one, how to build its `Enhancer` (`build_enhancer`) and when it applies
   (`can_run`).
2. `model_cls.model_fields` on each Pydantic config — which fields that block
   takes.
3. Each enhancer's uniform `enhance(analysis) -> Analysis` contract — how the
   block transforms the analysis.

## 2. Module map

```
enpkg/monolith/gui/
├── __init__.py
├── app.py            Streamlit entry point (layout, session state, glue)
├── blocks.py         Pipeline-block registry (id → enhancer builder, can_run, config, deps)
├── form_builder.py   Pydantic BaseModel → Streamlit widget tree
├── config_io.py      Unified YAML load/save + config instantiation
└── runner.py         Pipeline execution, DBLoader sharing, log streaming
```

### 2.1 [blocks.py](blocks.py) — the registry

A single `BLOCKS` list of `BlockSpec` frozen dataclasses. Each entry pins
together:

- `id` — short string key used in YAML, session state and widget keys;
- `label` — human-readable name shown in sidebar and tabs;
- `build_enhancer` — `(config, BuildContext) -> Enhancer`: how to construct the
  block's enhancer from the run's shared resources (logger, DBLoader,
  LotusStore);
- `can_run` — `(Analysis) -> bool`: the applicability guard (e.g. taxonomical
  needs a source taxon; weights needs a molecular network);
- `config_cls` — the Pydantic `EnhancerConfig` (or `None` for taxonomical);
- `log_summary` — post-run report function (see the module docstring);
- `description` — tooltip text;
- `depends_on` — block ids that must also be selected (e.g. `weights` →
  `network`).

The list is **ordered** — it defines the canonical pipeline execution order.
`BLOCKS_BY_ID` gives O(1) lookup. `MS_SHARED_BLOCKS`/`MS_SHARED_KEY` mark the
MS1/MS2 pair that shares a single `MSEnhancerConfig`.

Because every enhancer honours the uniform `enhance(analysis) -> Analysis`
contract, there is **no per-block step class** — the runner wraps
`build_enhancer` + `can_run` into one generic step. Adding a new pipeline block
is therefore *only* adding one `BlockSpec` entry (with a `build_enhancer`
closure and a `can_run` predicate); no new step file and no `_build_step`
branch are needed.

### 2.2 [form_builder.py](form_builder.py) — schema-driven forms

`render_model(model_cls, current, key_prefix, exclude_fields)` walks
`model_cls.model_fields` and renders a Streamlit widget per field. The return
value is a plain dict that can be fed straight into
`model_cls.model_validate(...)`.

Type-to-widget mapping:

| Annotation                  | Widget                             |
|-----------------------------|------------------------------------|
| `bool`                      | `st.checkbox`                      |
| `int` (with `ge`/`le`)      | `st.number_input(step=1)`          |
| `float` (with `ge`/`le`)    | `st.number_input`                  |
| `str` with `^(a\|b)$` regex | `st.selectbox`                     |
| long `str`                  | `st.text_area`                     |
| short `str`                 | `st.text_input`                    |
| `Optional[T]`               | widget for `T`                     |
| `list` / `tuple`            | `text_area`, one value per line    |
| nested `BaseModel`          | `st.expander` + recursive call     |

Field `description` becomes the widget `help=` tooltip. `ge`/`le`/`gt`/`lt`
constraints become widget bounds. The `_pattern_choices` helper picks up the
`^(pos|neg)$` shape used for polarity-style enums.

`exclude_fields` is how the app lifts the shared `general_params` out of every
tab — the field is skipped during rendering and re-injected later.

**Nothing here duplicates Pydantic rules.** The widgets only *pre-filter* what
the user can type; Pydantic still runs the authoritative validation when the
dict is turned back into a model instance.

### 2.3 [config_io.py](config_io.py) — YAML ↔ configs

Three functions:

- `load_unified_yaml(path)` — reads the YAML into a `{block_id: section}`
  dict. No validation.
- `get_section(data, block_id)` — looks up a block's section, mapping the
  MS1/MS2 pair onto the shared `ms_enhancer` key.
- `save_unified_yaml(path, validated_configs)` — dumps validated Pydantic
  models back to YAML, deduplicating MS1/MS2 into `ms_enhancer`.
- `build_configs(selected_ids, form_state)` — calls
  `config_cls.model_validate(form_state[block_id])` for every selected block
  and returns `{block_id: BaseModel}`. Raises `pydantic.ValidationError` on
  the first invalid section.

The YAML shape is intentionally flat:

```yaml
network:
  general_params: { recompute: false, polarity: pos }
  mn_msms_mz_tol: 0.01
  mn_score_cutoff: 0.7
  mn_top_n: 15
  mn_max_links: 10
ms_enhancer:
  general_params: { recompute: false, polarity: pos }
  downloader_params: { ... }
  spectral_match_params: { ... }
sirius:
  general_params: { recompute: false, polarity: pos }
  sirius_params: { ... }
weights:
  general_params: { recompute: false, polarity: pos }
  reweighting_params: { ... }
  downloader_params: { ... }
```

`general_params` is written into each section by the app before saving, so
the YAML stays self-contained even though the GUI edits it in one place.

### 2.4 [runner.py](runner.py) — execution

`run_pipeline(selected_ids, configs, spectra_path, metadata_path, quant_path,
ionization_mode, database_dir, log_queue) -> RunResult`.

Steps:

1. Attach a `QueueLogHandler` to a named logger so every log record is pushed
   onto a thread-safe queue that the Streamlit app can drain.
2. Call `AnalysisLoader.from_files(...)` to build an `Analysis`.
3. If any MS block is selected, build a single `DBLoader` from the
   `MSEnhancerConfig` and force its `downloader_params.download_dir` to
   `DATABASE_DIR`. The same `DBLoader` is reused by `ms1`, `ms2` and
   `weights` (all three need database access).
4. Iterate `BLOCKS` in canonical order. For each selected block:
   - skip if any `depends_on` entry is not also selected,
   - bind the block to its config + shared resources via `_build_step` — a
     generic `_BoundBlock` that wraps the registry's `build_enhancer` /
     `can_run` (there are no per-block step classes),
   - skip if `can_run(analysis)` is False,
   - build the enhancer and call `enhance(analysis)`, replacing the working
     `analysis`.
5. Return a `RunResult` with the final `analysis`, the list of executed
   blocks, skipped blocks, and any error message.

The runner and the `blocks` registry are the only modules that import enhancer
classes; everything upstream works off `BLOCKS`.

### 2.5 [app.py](app.py) — Streamlit glue

Owns layout, session state, and the user-action loop. It is the only module
that touches `st.session_state`. The session-state slots:

| Key                | Purpose                                                |
|--------------------|--------------------------------------------------------|
| `form_state`       | `{block_id: raw dict from render_model}`               |
| `selected_blocks`  | `{block_id: bool}` checkbox state                       |
| `general_params`   | Shared `GeneralParams` dict, edited above the tabs     |
| `input_dir`        | Current input folder (user-chosen in sidebar)          |
| `log_buffer`       | Drained log lines from the last run                    |
| `last_result`      | Last `RunResult` for the execution panel               |

The layout:

- **Sidebar**: config YAML load/save, block checkboxes, input-folder picker
  with validation, file dropdowns populated from that folder, databases-dir
  caption, Run button.
- **Main area**: the shared `General parameters` form, then one tab per
  selected block (MS1 and MS2 share a single "MS1 / MS2 (shared)" tab), then
  a validated-JSON preview, then the execution panel.

#### 2.5.1 Shared `general_params` — the key interaction

`GeneralParams` lives on every `EnhancerConfig`. Editing it inside each tab
would force the user to keep six copies in sync and let them silently drift.
Instead:

1. The app renders `GeneralParams` **once**, above the tabs, via
   `_render_general_params()` → writes to `st.session_state.general_params`.
2. When each block's form is rendered, `render_model(..., exclude_fields={
   "general_params"})` skips that field entirely.
3. Before validation, `_inject_shared_params(raw, shared)` folds the shared
   dict back into every block's raw values. Only blocks whose `config_cls`
   actually declares `general_params` receive it.
4. The validated configs therefore all carry **the same** `general_params`,
   and `save_unified_yaml` writes that identical dict under every section.
5. On load, `_load_config_into_state` reads `general_params` out of the first
   section that has one and seeds the shared widget.

`polarity` lives in `GeneralParams`, so it also drives
`AnalysisLoader.from_files(ionization_mode=...)` — the sidebar no longer has
a separate polarity selector.

#### 2.5.2 Input-folder picker

`DATABASE_DIR` is still fixed (`gui_workspace/databases/`). The input folder
is user-editable:

1. Sidebar `text_input` holds the current path, defaulted to
   `gui_workspace/input/`.
2. "Use this folder" button validates with `Path.is_dir()` and, on success,
   writes to `st.session_state.input_dir`.
3. The three file dropdowns (`spectra`, `metadata`, `quant`) list files in
   that folder filtered by extension.
4. The runner receives resolved absolute paths built from the stored
   `input_dir` plus the chosen filename.

## 3. Data flow

### 3.1 Top-level flow

```mermaid
flowchart LR
    User([User])
    Sidebar[Sidebar<br/>checkboxes,<br/>file picker]
    General[General params<br/>form]
    Tabs[Per-block tabs<br/>form_builder]
    Inject[inject shared<br/>general_params]
    Validate[config_io.build_configs<br/>Pydantic validate]
    Preview[Validated JSON<br/>preview]
    SaveYaml[save_unified_yaml]
    Runner[runner.run_pipeline]
    Exec[Execution panel<br/>logs + metrics]

    User --> Sidebar
    User --> General
    User --> Tabs
    Sidebar --> Runner
    General --> Inject
    Tabs --> Inject
    Inject --> Validate
    Validate --> Preview
    Validate --> SaveYaml
    Validate --> Runner
    Runner --> Exec
```

### 3.2 Module dependencies

```mermaid
flowchart TD
    app[app.py]
    blocks[blocks.py]
    form[form_builder.py]
    io[config_io.py]
    runner[runner.py]

    config[configuration/*.py<br/>Pydantic models]
    enhancers[enhancers/*.py]
    loader[loaders/analysis_loader.py]
    dbloader[loaders/database_loader.py]

    app --> form
    app --> io
    app --> runner
    app --> blocks
    app --> config

    io --> blocks
    io --> config

    runner --> blocks
    runner --> loader
    runner --> dbloader
    runner --> config

    blocks --> enhancers
    blocks --> config

    form --> config
```

The arrows show import direction. The GUI depends on the pipeline code, not
the other way around.

### 3.3 One run, end-to-end

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant A as app.py
    participant F as form_builder
    participant I as config_io
    participant R as runner
    participant DB as DBLoader
    participant S as Enhancer(s)

    U->>A: tick "network", "ms1", "ms2"
    U->>A: edit General params (polarity=neg)
    A->>F: render_model(each config, exclude=general_params)
    F-->>A: raw form dicts
    A->>A: _inject_shared_params(raw, general_params)
    A->>I: build_configs(selected, raw)
    I-->>A: {block_id: validated model}
    U->>A: click "Run pipeline"
    A->>R: run_pipeline(selected, configs, paths, polarity, db_dir, queue)
    R->>R: AnalysisLoader.from_files(...)
    R->>DB: new DBLoader(ms_config)
    loop each selected block in canonical order
        R->>R: bind block (build_enhancer + can_run)
        R->>R: can_run(analysis)?
        R->>S: enhance(analysis)
        S-->>R: enriched analysis
    end
    R-->>A: RunResult(analysis, executed, skipped, error)
    A->>U: logs + metrics in execution panel
```

## 4. Conventions and invariants

- **Registry is the source of truth.** The sidebar, the tabs, the YAML
  sections, and the runner loop all iterate `BLOCKS`. Never add a block by
  touching only one of these sites.
- **Pydantic owns validation.** The form builder never re-implements a
  constraint that exists in a `Field(...)` definition. If Pydantic says it's
  invalid, the app surfaces the `ValidationError` verbatim.
- **Session state is keyed by block id.** Widget keys follow
  `"{block_id}.{field_path}"` so toggling a block off and back on preserves
  its values.
- **Shared sub-configs are rendered once.** Currently only `general_params`;
  the pattern (render out-of-band + `exclude_fields` + `_inject_shared_params`)
  generalizes if more shared sub-models appear.
- **Workspace split.** `gui_workspace/databases/` is fixed and owned by the
  `DBLoader`; `gui_workspace/input/` is the **default** input folder but the
  user can point elsewhere at runtime.
- **The runner is the only orchestrator.** The app never calls enhancers
  directly; that keeps UI concerns (logging, error surfacing) separate from
  pipeline concerns.

## 5. Extending the GUI

- **Add a new pipeline block**: create the enhancer (honouring
  `enhance(analysis) -> Analysis`) + its config, then add one `BlockSpec` entry
  to `BLOCKS` with a `build_enhancer` closure and a `can_run` predicate. No step
  file and no `_build_step` branch are needed — the sidebar checkbox, tab, form,
  YAML section, execution slot and runner wiring all appear automatically.
- **Add a new shared sub-config**: render it above the tabs like
  `general_params`, store it in session state, add it to the
  `exclude_fields` set of affected tabs, and inject it back in
  `_inject_shared_params`.
- **Add a custom widget**: extend `_render_field` in
  [form_builder.py](form_builder.py) — it's a single function, no plugin
  machinery.
- **Replace the file-picker**: swap `_list_input_files` and the sidebar
  `text_input` block; the rest of the app only touches
  `st.session_state.input_dir`.
