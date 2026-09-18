"""Reading a configuration file into the stored settings.

Loading happens on the Imports page, and the widgets it fills are on the Pipeline page,
which is not built at that moment. So loading writes to the per-visitor store instead,
and the Pipeline page seeds its widgets from that store when it is next opened.

That is also the reason the load path takes no elements: nothing here touches a widget,
so it is testable without a page.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import BLOCKS, MS_SHARED_BLOCKS

# MS1 and MS2 share one config, so their sections are stored under one key.
MS_FORM_KEY = "ms1"


@dataclass
class LoadedConfig:
    """What a configuration file said, ready to be stored."""

    selected_blocks: list[str] = field(default_factory=list)
    form_state: dict[str, dict] = field(default_factory=dict)
    general_params: dict = field(default_factory=dict)
    serializer: dict = field(default_factory=dict)
    # Set when the file records no selection, which a file written before that key
    # existed does not. The selection cannot be recovered from the section names, so the
    # caller is told rather than being given a guess.
    warning: Optional[str] = None


def read_config(path: Path) -> LoadedConfig:
    """Read ``path`` into the values the Pipeline page builds its widgets from.

    Raises:
        OSError: the path does not name a readable file.
        ValueError: the file does not hold a mapping.
    """
    # Checked here because load_unified_yaml answers a missing file with an empty
    # mapping. That is right for a caller starting from nothing, but as a response to
    # Load it would report success and quietly deselect every block.
    if not path.is_file():
        raise OSError(f"Not a file: {path}")

    data = config_io.load_unified_yaml(path)

    selection = config_io.get_selection(data)
    warning = None
    if selection is None:
        warning = (
            f"{path.name} records no block selection, so which blocks to run cannot be "
            "determined. Tick them on the Pipeline page."
        )
        selection = []

    shared: Optional[dict] = None
    form_state: dict[str, dict] = {}
    for block in BLOCKS:
        if block.config_cls is None:
            continue
        section = config_io.get_section(data, block.id)
        if shared is None and isinstance(section.get(config_io.SHARED_FIELD), dict):
            shared = section[config_io.SHARED_FIELD]
        key = MS_FORM_KEY if block.id in MS_SHARED_BLOCKS else block.id
        form_state[key] = section

    return LoadedConfig(
        selected_blocks=list(selection),
        form_state=form_state,
        general_params=shared or GeneralParams().model_dump(),
        serializer=config_io.get_serializer_section(data) or SerializerConfig().model_dump(),
        warning=warning,
    )


def apply_to_state(loaded: LoadedConfig) -> None:
    """Write a loaded configuration into the per-visitor store.

    Every key is replaced, not merged: a file describes a whole run, so a block it does
    not mention must return to its defaults rather than keep whatever was there.
    """
    from enpkg.monolith.webui import state

    state.update(
        {
            "selected_blocks": loaded.selected_blocks,
            "form_state": loaded.form_state,
            "general_params": loaded.general_params,
            "serializer": loaded.serializer,
        }
    )
