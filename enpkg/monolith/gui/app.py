"""Streamlit entry point for the ENPKG pipeline GUI.

Run with::

    streamlit run enpkg/monolith/gui/app.py
"""
from __future__ import annotations

import logging
import queue
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.gui import config_io
from enpkg.monolith.gui.batch_runner import (
    BatchResult,
    discover_experiments,
    find_shared_metadata,
    run_batch,
)
from enpkg.monolith.gui.blocks import BLOCKS, BLOCKS_BY_ID, MS_SHARED_BLOCKS, MS_SHARED_KEY
from enpkg.monolith.gui.form_builder import render_model
from enpkg.monolith.gui.runner import run_pipeline

SHARED_FIELD = "general_params"


# --- Default workspace layout ----------------------------------------------
# The database directory is fixed for now. The input directory can be changed
# from the sidebar at runtime and is persisted in session state.
WORKSPACE_DIR: Path = Path("gui_workspace")
DEFAULT_INPUT_DIR: Path = WORKSPACE_DIR / "input"
DEFAULT_BATCH_DIR: Path = WORKSPACE_DIR / "batch_input"
DATABASE_DIR: Path = WORKSPACE_DIR / "databases"
DEFAULT_CONFIG_PATH: Path = WORKSPACE_DIR / "gui_config.yaml"


# Remove the noisy watchdog inotify_buffer logs
logging.getLogger("watchdog.observers.inotify_buffer").setLevel(logging.WARNING)   

def _ensure_workspace() -> None:
    """
    Ensure the default workspace directories exist. 
    If they don't, create them. 
    """
    DEFAULT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_BATCH_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)


def _list_input_files(input_dir: Path, suffixes: tuple[str, ...]) -> list[str]:
    """
    List files in the input directory that match the given suffixes, sorted alphabetically.

    Args:
        input_dir: The directory to scan for input files.
        suffixes: A tuple of file suffixes to filter by (e.g., (".mgf", ".mzml")).
    
    Returns:
        A sorted list of matching file names. If the input directory does not exist, returns an empty list.
    """
    if not input_dir.exists():
        return []
    return sorted(
        p.name
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in suffixes
    )


def _init_state() -> None:
    """
    Initialize the Streamlit session state with default values for form state, 
    selected blocks, log buffer, last results, and input directories.
    Do not forget to modify this function if you add new state variables that need defaults.
    """
    if "form_state" not in st.session_state:
        st.session_state.form_state = {}
    if "selected_blocks" not in st.session_state:
        st.session_state.selected_blocks = {b.id: False for b in BLOCKS}
    if "log_buffer" not in st.session_state:
        st.session_state.log_buffer = []
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "last_batch_result" not in st.session_state:
        st.session_state.last_batch_result = None
    if "general_params" not in st.session_state:
        st.session_state.general_params = GeneralParams().model_dump()
    if "input_dir" not in st.session_state:
        st.session_state.input_dir = str(DEFAULT_INPUT_DIR)
    if "batch_dir" not in st.session_state:
        st.session_state.batch_dir = str(DEFAULT_BATCH_DIR)
    if "batch_mode" not in st.session_state:
        st.session_state.batch_mode = False
    if "_prev_sirius_spectra" not in st.session_state:
        st.session_state["_prev_sirius_spectra"] = None


def _load_config_into_state(path: Path) -> None:
    """
    Load a unified YAML config from the given path and populate the session state form_state and general_params.
    """
    data = config_io.load_unified_yaml(path)
    shared = None
    for block in BLOCKS:
        # For blocks with no config_cls, we skip loading since they have no parameters to populate.
        if block.config_cls is None:
            continue
        # 

        section = config_io.get_section(data, block.id)
        if shared is None and isinstance(section.get(SHARED_FIELD), dict):
            shared = section[SHARED_FIELD]
        st.session_state.form_state[block.id] = section
    if shared is not None:
        st.session_state.general_params = shared
    st.success(f"Loaded config from {path}")


def _render_sidebar() -> tuple[list[str], dict]:
    st.sidebar.header("MS2KG pipelin")

    st.sidebar.subheader("Config file")
    config_path_str = st.sidebar.text_input(
        "YAML path",
        value=str(DEFAULT_CONFIG_PATH),
        help="Unified config YAML. Loaded on demand; saved when you click Save.",
    )
    config_path = Path(config_path_str)
    col_a, col_b = st.sidebar.columns(2)
    if col_a.button("Load", use_container_width=True):
        try:
            _load_config_into_state(config_path)
        except Exception as exc:
            st.sidebar.error(f"Load failed: {exc}")
    save_clicked = col_b.button("Save", use_container_width=True)

    st.sidebar.subheader("Blocks to run")
    for block in BLOCKS:
        st.session_state.selected_blocks[block.id] = st.sidebar.checkbox(
            block.label,
            value=st.session_state.selected_blocks.get(block.id, False),
            key=f"select.{block.id}",
            help=block.description,
        )
    selected = [b.id for b in BLOCKS if st.session_state.selected_blocks[b.id]]

    st.sidebar.subheader("Input data")
    st.session_state.batch_mode = st.sidebar.checkbox(
        "Batch mode (many experiments)",
        value=st.session_state.batch_mode,
        help="Run every experiment under a parent folder. Expects a shared "
             "metadata file at the parent and one subfolder per experiment "
             "(each with its own spectra + quant files).",
    )

    sidebar_extra: dict = {}
    if st.session_state.batch_mode:
        sidebar_extra = _render_batch_sidebar()
    else:
        sidebar_extra = _render_single_sidebar()

    st.sidebar.caption(f"Databases dir: `{DATABASE_DIR}`")
    verbose = st.sidebar.checkbox("Verbose logging", value=False)
    run_clicked = st.sidebar.button("Run pipeline", type="primary", use_container_width=True)

    return selected, {
        "config_path": config_path,
        "save_clicked": save_clicked,
        "run_clicked": run_clicked,
        "verbose": verbose,
        **sidebar_extra,
    }


def _render_single_sidebar() -> dict:
    """Render the single-experiment file pickers and return their selections."""
    input_dir_str = st.sidebar.text_input(
        "Input folder",
        value=st.session_state.input_dir,
        help="Folder containing spectra, metadata and quant files.",
    )
    if st.sidebar.button("Use this folder", use_container_width=True):
        candidate = Path(input_dir_str).expanduser()
        if candidate.is_dir():
            st.session_state.input_dir = str(candidate)
            st.sidebar.success(f"Input folder set to {candidate}")
        else:
            st.sidebar.error(f"Not a directory: {candidate}")
    input_dir = Path(st.session_state.input_dir).expanduser()
    st.sidebar.caption(f"Scanning `{input_dir}`")

    spectra_files = _list_input_files(input_dir, (".mgf", ".mzml", ".mzxml"))
    metadata_files = _list_input_files(input_dir, (".tsv", ".txt", ".csv"))
    quant_files = _list_input_files(input_dir, (".csv", ".tsv"))
    spectra = st.sidebar.selectbox("Spectra", spectra_files or ["(none found)"])
    metadata = st.sidebar.selectbox("Metadata", metadata_files or ["(none found)"])
    quant = st.sidebar.selectbox("Quant table", quant_files or ["(none found)"])
    selectboxes_states = {
            "input_dir": input_dir,
            "spectra": spectra if spectra_files else None,
            "metadata": metadata if metadata_files else None,
            "quant": quant if quant_files else None,
        }
    
    if "sirius" in st.session_state.selected_blocks:
        sirius_files = [f for f in spectra_files if "_sirius" in f.lower() and f.lower().endswith(".mgf")]
        sirius_spectra = st.sidebar.selectbox("Spectra for Sirius", sirius_files or ["(none found)"], key="sirius_spectra")
        selectboxes_states["sirius_spectra"] = sirius_spectra
    
    return selectboxes_states


def _render_batch_sidebar() -> dict:
    """Render the batch-mode parent folder picker and discovery preview."""
    batch_dir_str = st.sidebar.text_input(
        "Batch parent folder",
        value=st.session_state.batch_dir,
        help="Parent folder containing a shared metadata file + one subfolder per experiment.",
    )
    if st.sidebar.button("Use this folder", use_container_width=True):
        candidate = Path(batch_dir_str).expanduser()
        if candidate.is_dir():
            st.session_state.batch_dir = str(candidate)
            st.sidebar.success(f"Batch folder set to {candidate}")
        else:
            st.sidebar.error(f"Not a directory: {candidate}")
    batch_dir = Path(st.session_state.batch_dir).expanduser()

    metadata_path = find_shared_metadata(batch_dir) if batch_dir.is_dir() else None
    experiments = discover_experiments(batch_dir) if batch_dir.is_dir() else []

    if metadata_path is None:
        st.sidebar.error("No shared metadata file found at parent root.")
    else:
        st.sidebar.caption(f"Metadata: `{metadata_path.name}`")
    st.sidebar.caption(f"Experiments discovered: {len(experiments)}")
    if experiments:
        with st.sidebar.expander("Preview", expanded=False):
            for exp in experiments:
                st.write(f"- **{exp.run_name}** — `{exp.subfolder.name}/`")

    return {
        "batch_dir": batch_dir,
        "batch_metadata": metadata_path,
        "batch_experiments": experiments,
    }


def _render_general_params() -> dict:
    """Render the shared GeneralParams form above the tabs."""
    st.subheader("General parameters")
    st.caption("Shared across every block that accepts them.")
    values = render_model(
        GeneralParams,
        st.session_state.general_params,
        key_prefix="shared.general_params",
    )
    st.session_state.general_params = values
    return values


def _render_forms(selected: list[str]) -> dict[str, dict]:
    """Render a tab per selected block and return the raw form dicts."""
    if not selected:
        st.info("Select at least one pipeline block in the sidebar to configure.")
        return {}

    # Deduplicate MS1/MS2 into a single tab for the shared config.
    tab_ids: list[str] = []
    for bid in selected:
        if bid in MS_SHARED_BLOCKS and MS_SHARED_KEY in tab_ids:
            continue
        tab_ids.append(MS_SHARED_KEY if bid in MS_SHARED_BLOCKS else bid)

    labels: list[str] = []
    for tid in tab_ids:
        if tid == MS_SHARED_KEY:
            labels.append("MS1 / MS2 (shared)")
        else:
            labels.append(BLOCKS_BY_ID[tid].label)

    tabs = st.tabs(labels)
    raw: dict[str, dict] = {}
    for tab, tid in zip(tabs, tab_ids):
        with tab:
            if tid == MS_SHARED_KEY:
                block = BLOCKS_BY_ID["ms1"]
                st.caption("This configuration is shared by both MS1 and MS2 enhancement steps.")
                current = st.session_state.form_state.get("ms1") or st.session_state.form_state.get("ms2") or {}
                values = render_model(
                    block.config_cls,
                    current,
                    key_prefix="ms_enhancer",
                    exclude_fields={SHARED_FIELD},
                )
                st.session_state.form_state["ms1"] = values
                st.session_state.form_state["ms2"] = values
                raw["ms1"] = values
                raw["ms2"] = values
            else:
                block = BLOCKS_BY_ID[tid]
                if block.config_cls is None:
                    st.info(f"{block.label} has no configurable parameters.")
                    continue
                current = st.session_state.form_state.get(tid) or {}
                values = render_model(
                    block.config_cls,
                    current,
                    key_prefix=tid,
                    exclude_fields={SHARED_FIELD},
                )
                st.session_state.form_state[tid] = values
                raw[tid] = values

    return raw


def _inject_shared_params(raw: dict[str, dict], shared: dict) -> dict[str, dict]:
    """Merge the shared general_params into every block's form dict."""
    merged: dict[str, dict] = {}
    for block_id, values in raw.items():
        block = BLOCKS_BY_ID[block_id]
        if block.config_cls is not None and SHARED_FIELD in block.config_cls.model_fields:
            merged[block_id] = {**values, SHARED_FIELD: shared}
        else:
            merged[block_id] = values
    return merged


def _build_configs_from_raw(selected: list[str], raw: dict[str, dict]):
    """
    Instantiate Pydantic config objects from the raw form dicts 
    and return them in a dict, or return the validation error if any.
    """
    try:
        return config_io.build_configs(selected, raw), None
    except ValidationError as exc:
        return None, exc


def __check_and_display_if_valid(configs: dict, error):
    """
    Display if the current configs that have been built from the
    raw form states are valid or if there are any errors on build.
    """
    st.subheader("Validated configuration")
    if error is not None:
        st.error("Configuration is invalid:")
        st.code(str(error))
        return
    if not configs:
        st.caption("(nothing to preview)")
        return
    for block_id, cfg in configs.items():
        if cfg is None:
            continue
        with st.expander(f"{BLOCKS_BY_ID[block_id].label} — validated JSON"):
            st.code(cfg.model_dump_json(indent=2), language="json")


def _render_execution(result) -> None:
    st.subheader("Execution")
    if result is None:
        st.caption("Run the pipeline to see logs and results here.")
        return
    if result.error:
        st.error(result.error)
    else:
        st.success(f"Pipeline finished. Executed: {', '.join(result.executed) or 'none'}")
    if result.skipped:
        st.warning(f"Skipped: {', '.join(result.skipped)}")
    if result.log_file:
        st.info(f"Runtime log: `{result.log_file}`")
    if result.summary_file:
        st.info(f"Summary log: `{result.summary_file}`")
    if st.session_state.log_buffer:
        with st.expander("Run logs", expanded=False):
            st.code("\n".join(st.session_state.log_buffer), language="text")
    _render_analysis_metrics(result.analysis)


def _render_batch_execution(batch: BatchResult) -> None:
    """
    Render the batch execution results, including a summary 
    of succeeded/failed experiments and details for each experiment.

    Args:
        batch: The BatchResult object containing the results of the batch execution.
    """
    st.subheader("Batch execution")
    if batch is None:
        st.caption("Run the pipeline to see logs and results here.")
        return
    if batch.error:
        st.error(batch.error)
        return
    cols = st.columns(3)
    cols[0].metric("Total", len(batch.results))
    cols[1].metric("Succeeded", len(batch.succeeded))
    cols[2].metric("Failed", len(batch.failed))
    if batch.runtime_log:
        st.info(f"Batch folder: `{batch.batch_dir}`")
    if batch.summary_log:
        st.info(f"Batch summary: `{batch.summary_log}`")
    if st.session_state.log_buffer:
        with st.expander("Combined run logs", expanded=False):
            st.code("\n".join(st.session_state.log_buffer), language="text")
    for r in batch.results:
        name = r.analysis.run_name if r.analysis is not None else (
            r.log_file.parent.name if r.log_file else "(unknown)"
        )
        status_icon = "❌" if r.error else "✅"
        with st.expander(f"{status_icon} {name}", expanded=False):
            if r.error:
                st.error(r.error)
            else:
                st.success(f"Executed: {', '.join(r.executed) or 'none'}")
            if r.skipped:
                st.warning(f"Skipped: {', '.join(r.skipped)}")
            if r.log_file:
                st.caption(f"Runtime log: `{r.log_file}`")
            if r.summary_file:
                st.caption(f"Summary log: `{r.summary_file}`")
            _render_analysis_metrics(r.analysis)


def _render_analysis_metrics(analysis) -> None:
    if analysis is None:
        return
    cols = st.columns(3)
    cols[0].metric("Spectra", len(analysis.spectra))
    cols[1].metric("OTT matches", len(getattr(analysis, "ott_matches", []) or []))
    network = getattr(analysis, "molecular_network", None)
    cols[2].metric("Network nodes", len(network.nodes) if network is not None else 0)


def main() -> None:

    st.set_page_config(page_title="ENPKG pipeline", layout="wide")
    _ensure_workspace()
    _init_state()

    selected, sidebar = _render_sidebar()
    batch_mode = st.session_state.batch_mode

    # When the sidebar spectra selection changes, pre-populate the Sirius path field
    # (doesn't feel great this, maybe will change later). Only applies in single-run mode;
    # batch mode rewrites Sirius paths per-experiment inside the runner.
    # if not batch_mode:
    #     _spectra = sidebar.get("spectra")
    #     _input_dir = sidebar.get("input_dir")
        # if "sirius" in selected and _spectra and _input_dir is not None:
        #     spectra_full = str(_input_dir / _spectra)
        #     if spectra_full != st.session_state.get("_prev_sirius_spectra"):
        #         st.session_state["_prev_sirius_spectra"] = spectra_full
        #         st.session_state["sirius.sirius_params.path_to_input_spectra"] = spectra_full

    st.title("ENPKG pipeline configuration")
    shared_params = _render_general_params()
    raw = _render_forms(selected)
    raw = _inject_shared_params(raw, shared_params)
    configs, error = _build_configs_from_raw(selected, raw)
    __check_and_display_if_valid(configs or {}, error)

    if sidebar["save_clicked"]:
        if error is not None:
            st.error("Fix validation errors before saving.")
        else:
            try:
                config_io.save_unified_yaml(sidebar["config_path"], configs or {})
                st.success(f"Saved to {sidebar['config_path']}")
            except Exception as exc:
                st.error(f"Save failed: {exc}")

    run_just_finished = False
    if sidebar["run_clicked"]:
        if error is not None:
            st.error("Fix validation errors before running.")
        elif not selected:
            st.error("Select at least one block.")
        elif batch_mode:
            _run_batch_mode(selected, configs or {}, shared_params, sidebar)
            run_just_finished = True
        elif not all(sidebar.get(k) for k in ("spectra", "metadata", "quant")):
            st.error(
                f"Place spectra, metadata, and quant files in {sidebar['input_dir']} and pick them in the sidebar."
            )
        else:
            _run_single_mode(selected, configs or {}, shared_params, sidebar)
            run_just_finished = True

    # After a run, force a fresh rerun so the execution panel renders in a
    # clean script pass — rendering it in the same pass as the long-running
    # st.status block can leave the panel invisible below the collapsed status.
    if run_just_finished:
        st.rerun()

    if batch_mode:
        _render_batch_execution(st.session_state.last_batch_result)
    else:
        _render_execution(st.session_state.last_result)


def _run_single_mode(selected, configs, shared_params, sidebar) -> None:
    log_q: "queue.Queue[str]" = queue.Queue()
    input_dir = sidebar["input_dir"]
    with st.status("Running pipeline…", expanded=True) as status:
        result = run_pipeline(
            selected_ids=selected,
            configs=configs,
            spectra_path=input_dir / sidebar["spectra"],
            metadata_path=input_dir / sidebar["metadata"],
            quant_path=input_dir / sidebar["quant"],
            ionization_mode=shared_params.get("polarity", "pos"),
            database_dir=DATABASE_DIR,
            log_queue=log_q,
            verbose=sidebar["verbose"],
        )
        st.session_state.log_buffer = _drain_queue(log_q)
        st.session_state.last_result = result
        if result.error:
            status.update(label="Pipeline failed", state="error")
        else:
            status.update(label="Pipeline finished", state="complete")


def _run_batch_mode(selected, configs, shared_params, sidebar) -> None:
    """
    Run the batch pipeline on every experiment discovered under the selected 
    batch parent folder, using the shared metadata and the configs built from the form states.
    
    Args:
        selected: List of selected block IDs to run.
        configs: Dict of instantiated config objects for each block, built from the form states.
        shared_params: Dict of shared general parameters to inject into each block's config.
        sidebar: Dict of sidebar state values, including batch directory, discovered experiments, and metadata path
    """
    experiments = sidebar.get("batch_experiments") or []
    if sidebar.get("batch_metadata") is None:
        st.error(
            f"No shared metadata file found at {sidebar.get('batch_dir')}. "
            "Place a metadata.tsv/.txt/.csv at the parent folder root."
        )
        return
    if not experiments:
        st.error(
            f"No experiment subfolders with spectra + quant found under {sidebar.get('batch_dir')}."
        )
        return
    log_q: "queue.Queue[str]" = queue.Queue()
    with st.status(f"Running batch ({len(experiments)} experiments)…", expanded=True) as status:
        batch = run_batch(
            parent_dir=sidebar["batch_dir"],
            selected_ids=selected,
            configs=configs,
            ionization_mode=shared_params.get("polarity", "pos"),
            database_dir=DATABASE_DIR,
            log_queue=log_q,
            verbose=sidebar["verbose"],
        )
        st.session_state.log_buffer = _drain_queue(log_q)
        st.session_state.last_batch_result = batch
        if batch.error or batch.failed:
            status.update(
                label=f"Batch finished with errors ({len(batch.failed)} failed)",
                state="error",
            )
        else:
            status.update(label=f"Batch finished ({len(batch.succeeded)} ok)", state="complete")


def _drain_queue(q: "queue.Queue[str]") -> list[str]:
    out: list[str] = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            break
    return out


if __name__ == "__main__":
    main()
