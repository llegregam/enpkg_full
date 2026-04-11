"""Streamlit entry point for the ENPKG pipeline GUI.

Run with::

    streamlit run enpkg/monolith/gui/app.py
"""
from __future__ import annotations

import queue
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.gui import config_io
from enpkg.monolith.gui.blocks import BLOCKS, BLOCKS_BY_ID, MS_SHARED_BLOCKS, MS_SHARED_KEY
from enpkg.monolith.gui.form_builder import render_model
from enpkg.monolith.gui.runner import run_pipeline

SHARED_FIELD = "general_params"


# --- Default workspace layout ----------------------------------------------
# The database directory is fixed for now. The input directory can be changed
# from the sidebar at runtime and is persisted in session state.
WORKSPACE_DIR = Path("gui_workspace")
DEFAULT_INPUT_DIR = WORKSPACE_DIR / "input"
DATABASE_DIR = WORKSPACE_DIR / "databases"
DEFAULT_CONFIG_PATH = WORKSPACE_DIR / "gui_config.yaml"


def _ensure_workspace() -> None:
    DEFAULT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)


def _list_input_files(input_dir: Path, suffixes: tuple[str, ...]) -> list[str]:
    if not input_dir.exists():
        return []
    return sorted(
        p.name
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in suffixes
    )


def _init_state() -> None:
    if "form_state" not in st.session_state:
        st.session_state.form_state = {}
    if "selected_blocks" not in st.session_state:
        st.session_state.selected_blocks = {b.id: False for b in BLOCKS}
    if "log_buffer" not in st.session_state:
        st.session_state.log_buffer = []
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "general_params" not in st.session_state:
        st.session_state.general_params = GeneralParams().model_dump()
    if "input_dir" not in st.session_state:
        st.session_state.input_dir = str(DEFAULT_INPUT_DIR)


def _load_config_into_state(path: Path) -> None:
    data = config_io.load_unified_yaml(path)
    shared = None
    for block in BLOCKS:
        if block.config_cls is None:
            continue
        section = config_io.get_section(data, block.id)
        if shared is None and isinstance(section.get(SHARED_FIELD), dict):
            shared = section[SHARED_FIELD]
        st.session_state.form_state[block.id] = section
    if shared is not None:
        st.session_state.general_params = shared
    st.success(f"Loaded config from {path}")


def _render_sidebar() -> tuple[list[str], dict]:
    st.sidebar.header("ENPKG pipeline")

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
    st.sidebar.caption(f"Databases dir: `{DATABASE_DIR}`")

    run_clicked = st.sidebar.button("Run pipeline", type="primary", use_container_width=True)

    return selected, {
        "config_path": config_path,
        "save_clicked": save_clicked,
        "run_clicked": run_clicked,
        "input_dir": input_dir,
        "spectra": spectra if spectra_files else None,
        "metadata": metadata if metadata_files else None,
        "quant": quant if quant_files else None,
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


def _validate(selected: list[str], raw: dict[str, dict]):
    try:
        return config_io.build_configs(selected, raw), None
    except ValidationError as exc:
        return None, exc


def _render_validation_preview(configs: dict, error):
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
    if st.session_state.log_buffer:
        st.code("\n".join(st.session_state.log_buffer), language="text")
    analysis = result.analysis
    if analysis is not None:
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

    st.title("ENPKG pipeline configuration")
    shared_params = _render_general_params()
    raw = _render_forms(selected)
    raw = _inject_shared_params(raw, shared_params)
    configs, error = _validate(selected, raw)
    _render_validation_preview(configs or {}, error)

    if sidebar["save_clicked"]:
        if error is not None:
            st.error("Fix validation errors before saving.")
        else:
            try:
                config_io.save_unified_yaml(sidebar["config_path"], configs or {})
                st.success(f"Saved to {sidebar['config_path']}")
            except Exception as exc:
                st.error(f"Save failed: {exc}")

    if sidebar["run_clicked"]:
        if error is not None:
            st.error("Fix validation errors before running.")
        elif not selected:
            st.error("Select at least one block.")
        elif not all(sidebar[k] for k in ("spectra", "metadata", "quant")):
            st.error(
                f"Place spectra, metadata, and quant files in {sidebar['input_dir']} and pick them in the sidebar."
            )
        else:
            log_q: "queue.Queue[str]" = queue.Queue()
            input_dir = sidebar["input_dir"]
            with st.status("Running pipeline…", expanded=True) as status:
                result = run_pipeline(
                    selected_ids=selected,
                    configs=configs or {},
                    spectra_path=input_dir / sidebar["spectra"],
                    metadata_path=input_dir / sidebar["metadata"],
                    quant_path=input_dir / sidebar["quant"],
                    ionization_mode=shared_params.get("polarity", "pos"),
                    database_dir=DATABASE_DIR,
                    log_queue=log_q,
                )
                st.session_state.log_buffer = _drain_queue(log_q)
                st.session_state.last_result = result
                if result.error:
                    status.update(label="Pipeline failed", state="error")
                else:
                    status.update(label="Pipeline finished", state="complete")

    _render_execution(st.session_state.last_result)


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
