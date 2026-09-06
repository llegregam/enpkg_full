"""Unified YAML load/save for a pipeline run's configuration.

The YAML is a single file with top-level keys matching block ids, plus a
shared ``ms_enhancer`` key for the MSEnhancerConfig used by both MS1 and MS2.
Missing sections are simply absent.

Alongside the config sections, the file records which blocks were selected
under the ``selected_blocks`` key. The section keys alone cannot stand in for
that: blocks with no ``config_cls`` (taxonomical) never produce a section, and
MS1/MS2 collapse into the single ``ms_enhancer`` key, so three of the seven
blocks are unrecoverable from the section names. Recording the selection
explicitly keeps the file a complete description of a run — load it and you
reproduce that run, blocks included.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from enpkg.monolith.pipeline.blocks import BLOCKS, BLOCKS_BY_ID, MS_SHARED_BLOCKS, MS_SHARED_KEY

# Top-level YAML key holding the list of selected block ids. Not a block id, so
# ``get_section`` (which looks sections up by block id) never collides with it.
SELECTION_KEY = "selected_blocks"


def load_unified_yaml(path: Path) -> dict[str, dict]:
    """Return a mapping ``{block_id_or_shared_key: section_dict}``.

    The MS1 and MS2 blocks both read their section from the shared
    ``ms_enhancer`` key if present.

    Args:
        path: The path to the YAML file. If the file does not exist, an empty dict is returned.
    Raises:
        ValueError: If the YAML file is not a mapping at the top level.
    Returns:
        A dict mapping block ids (and the shared key) to their respective section dicts.
    """
    if not path.exists():
        return {}
    with path.open("r") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config YAML at {path} must be a mapping at the top level.")
    return data


def get_section(data: dict[str, dict], block_id: str) -> dict:
    """Look up the section for a block, handling the MS1/MS2 shared key.
    Args:
        data: The full YAML data as returned by load_unified_yaml.
        block_id: The ID of the block for which to look up the section.
    Returns:
        The section dictionary for the specified block. For MS1/MS2, this will be the contents of the shared key if present, otherwise an empty dict.
    """
    if block_id in MS_SHARED_BLOCKS:
        return dict(data.get(MS_SHARED_KEY, {}))
    return dict(data.get(block_id, {}))


def get_selection(data: dict[str, Any]) -> list[str] | None:
    """Return the block ids recorded under ``selected_blocks``, or None.

    Args:
        data: The full YAML data as returned by load_unified_yaml.
    Raises:
        ValueError: If ``selected_blocks`` is present but is not a list.
    Returns:
        The selected block ids, filtered to those the registry still knows about
        (a block dropped from BLOCKS since the file was written is ignored rather
        than raising). ``None`` when the key is absent, which is how configs
        written before this key existed are recognised; an empty list is a
        genuine "nothing selected" and is distinct from ``None``.
    """
    raw = data.get(SELECTION_KEY)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(
            f"'{SELECTION_KEY}' must be a list of block ids, got {type(raw).__name__}."
        )
    return [block_id for block_id in raw if block_id in BLOCKS_BY_ID]


def save_unified_yaml(
    path: Path,
    validated_configs: dict[str, Any],
) -> None:
    """Dump validated Pydantic configs back to a single YAML file.

    ``validated_configs`` maps block id -> Pydantic BaseModel instance for
    every selected block. MS1/MS2 are deduplicated into ``ms_enhancer``.

    The keys of ``validated_configs`` *are* the selection (``build_configs``
    enters every selected block, storing ``None`` for the config-less ones), so
    the ``selected_blocks`` list is derived from them rather than passed in
    separately.

    Args:
        path: The path to the YAML file.
        validated_configs: A dictionary mapping block IDs to their validated Pydantic config instances.
            Blocks without a ``config_cls`` map to ``None`` and contribute to
            ``selected_blocks`` without producing a section of their own.
    """
    out: dict[str, Any] = {
        SELECTION_KEY: [b.id for b in BLOCKS if b.id in validated_configs],
    }
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

    Args:
        selected_ids: The list of block IDs that are currently selected in the GUI.
        form_state: A mapping from block ID to the raw dictionary of form values for that block.
    Returns:
        A dictionary mapping block IDs to their instantiated Pydantic config objects,
        based on the provided form state. For blocks without a config_cls, the value will be None.
    """
    result: dict[str, Any] = {}
    for block_id in selected_ids:
        block = BLOCKS_BY_ID[block_id]
        if block.config_cls is None:
            result[block_id] = None
            continue
        result[block_id] = block.config_cls.model_validate(form_state.get(block_id, {}))
    return result
