"""Shared helpers for the ``enpkg`` subcommands.

Config loading is the substantial part. A unified YAML holds one section per block plus
the block selection, and turning that into validated config objects takes the same four
steps for every command that runs something, in the same order. They live here so a
command cannot drift out of step with what the GUI does to the same file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from enpkg.monolith.configuration.config import GeneralParams
from enpkg.monolith.configuration.serializer_config import SerializerConfig
from enpkg.monolith.pipeline import config_io
from enpkg.monolith.pipeline.blocks import BLOCKS_BY_ID
from enpkg.monolith.pipeline.runner import STAGE_LABELS


def fail(message: str) -> None:
    """Print to stderr and exit non-zero."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def load_run_config(
    config_path: Path,
) -> tuple[list[str], dict[str, Any], SerializerConfig]:
    """Turn a unified YAML into ``(selected_ids, configs, serializer_config)``.

    The shared ``general_params`` is read from the first section carrying one and merged
    into every section that accepts it. Skipping that step would build each block from
    its Pydantic defaults, which for ``ionization_mode`` means processing a negative-mode
    dataset as positive.

    A file that cannot describe a run exits with a message instead of raising: no
    recorded selection, an empty selection, an unknown block id, or a section Pydantic
    rejects.
    """
    if not config_path.is_file():
        fail(f"Config file not found: {config_path}")

    try:
        data = config_io.load_unified_yaml(config_path)
    except Exception as exc:
        fail(f"Could not read {config_path}: {exc}")

    selected = config_io.get_selection(data)
    if selected is None:
        fail(
            f"{config_path} has no '{config_io.SELECTION_KEY}' key, so which blocks to "
            "run cannot be determined. Add it, or re-save the config from the GUI."
        )
    if not selected:
        fail(f"{config_path} selects no blocks.")

    unknown = [b for b in selected if b not in BLOCKS_BY_ID]
    if unknown:
        fail(f"Unknown block ids in {config_path}: {', '.join(unknown)}")

    raw = {block_id: config_io.get_section(data, block_id) for block_id in selected}
    shared = _first_general_params(raw)
    try:
        configs = config_io.build_configs(
            selected, config_io.inject_shared_params(raw, shared)
        )
        serializer_config = SerializerConfig.model_validate(
            config_io.get_serializer_section(data)
        )
    except ValidationError as exc:
        fail(f"Invalid configuration in {config_path}:\n{exc}")

    return selected, configs, serializer_config


def _first_general_params(raw: dict[str, dict]) -> dict:
    """Return the shared params from the first section carrying them, else defaults."""
    for section in raw.values():
        candidate = section.get(config_io.SHARED_FIELD)
        if isinstance(candidate, dict):
            return candidate
    return GeneralParams().model_dump()


def resolve_ionization_mode(configs: dict[str, Any], override: str | None) -> str:
    """Return the run's ionization mode, refusing a contradiction.

    The mode is both a field of every config's ``general_params`` and an argument to the
    runners, and nothing reconciles the two. An override that disagreed with the config
    would decide how the analysis is loaded while every block kept the config's value.
    """
    from_config = None
    for cfg in configs.values():
        general = getattr(cfg, config_io.SHARED_FIELD, None)
        if general is not None:
            from_config = general.ionization_mode
            break

    if override is None:
        return from_config or "pos"
    if from_config is not None and override != from_config:
        fail(
            f"--ionization-mode {override!r} contradicts the config's "
            f"general_params.ionization_mode {from_config!r}. Change one of them."
        )
    return override


def duration_rows(durations: dict[str, float]) -> list[tuple[str, float]]:
    """Return ``(label, seconds)`` pairs, slowest first.

    Stage keys such as ``__load__`` are named by ``STAGE_LABELS``; block ids are named by
    the registry, so a key is only shown raw when it is neither.
    """
    rows = []
    for key, seconds in durations.items():
        if key in STAGE_LABELS:
            label = STAGE_LABELS[key]
        elif key in BLOCKS_BY_ID:
            label = BLOCKS_BY_ID[key].label
        else:
            label = key
        rows.append((label, seconds))
    return sorted(rows, key=lambda row: row[1], reverse=True)
