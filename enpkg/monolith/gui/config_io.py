"""Unified YAML load/save for the GUI.

The YAML is a single file with top-level keys matching block ids, plus a
shared ``ms_enhancer`` key for the MSEnhancerConfig used by both MS1 and MS2.
Missing sections are simply absent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from enpkg.monolith.gui.blocks import BLOCKS, BLOCKS_BY_ID, MS_SHARED_BLOCKS, MS_SHARED_KEY


def load_unified_yaml(path: Path) -> dict[str, dict]:
    """Return a mapping ``{block_id_or_shared_key: section_dict}``.

    The MS1 and MS2 blocks both read their section from the shared
    ``ms_enhancer`` key if present.
    """
    if not path.exists():
        return {}
    with path.open("r") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config YAML at {path} must be a mapping at the top level.")
    return data


def get_section(data: dict[str, dict], block_id: str) -> dict:
    """Look up the section for a block, handling the MS1/MS2 shared key."""
    if block_id in MS_SHARED_BLOCKS:
        return dict(data.get(MS_SHARED_KEY, {}))
    return dict(data.get(block_id, {}))


def save_unified_yaml(
    path: Path,
    validated_configs: dict[str, Any],
) -> None:
    """Dump validated Pydantic configs back to a single YAML file.

    ``validated_configs`` maps block id -> Pydantic BaseModel instance for
    every selected block. MS1/MS2 are deduplicated into ``ms_enhancer``.
    """
    out: dict[str, Any] = {}
    seen_ms = False
    for block in BLOCKS:
        cfg = validated_configs.get(block.id)
        if cfg is None:
            continue
        if block.id in MS_SHARED_BLOCKS:
            if seen_ms:
                continue
            out[MS_SHARED_KEY] = cfg.model_dump(exclude_none=True)
            seen_ms = True
        else:
            out[block.id] = cfg.model_dump(exclude_none=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(out, f, sort_keys=False)


def build_configs(
    selected_ids: list[str],
    form_state: dict[str, dict],
) -> dict[str, Any]:
    """Instantiate Pydantic configs from raw form dicts.

    Raises pydantic.ValidationError on any invalid section. The caller is
    expected to surface that error to the user.
    """
    result: dict[str, Any] = {}
    for block_id in selected_ids:
        block = BLOCKS_BY_ID[block_id]
        if block.config_cls is None:
            result[block_id] = None
            continue
        result[block_id] = block.config_cls.model_validate(form_state.get(block_id, {}))
    return result
