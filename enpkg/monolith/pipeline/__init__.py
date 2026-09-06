"""Pipeline core: the block registry, the runners, and run configuration I/O.

Everything needed to configure and execute a pipeline run lives here, with no
dependency on any front-end. :mod:`enpkg.monolith.gui` builds a Streamlit UI on
top of this package; a headless caller uses the same entry points directly.

- :mod:`~enpkg.monolith.pipeline.blocks` — the registry of pipeline blocks and
  the ordering/resource rules derived from it.
- :mod:`~enpkg.monolith.pipeline.runner` — execute the selected blocks against
  one analysis.
- :mod:`~enpkg.monolith.pipeline.batch_runner` — the same across many
  experiments, sharing resources between them.
- :mod:`~enpkg.monolith.pipeline.config_io` — the unified run-configuration
  YAML.
- :mod:`~enpkg.monolith.pipeline.log_utils` — helpers shared by the per-block
  log summaries.
"""
