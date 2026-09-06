# Changelog

A running log of notable changes to the pipeline — what changed and, more importantly,
**why**. This is a working journal for humans and for Claude Code to follow what has been
done and understand the design decisions made along the way. It is not release notes tied
to version numbers.

## How to use this file

- Add an entry whenever you make a change worth remembering: a design decision, a
  behavioural change, a new pipeline component, a non-obvious fix, or a reversal of an
  earlier decision.
- Newest entries go at the top, under a date heading (`### YYYY-MM-DD`).
- Each entry is a short bullet list. Say what changed and why. If the change overturns an
  earlier decision, name that decision and explain what prompted the change.
- Routine changes (typos, formatting, dependency bumps with no behavioural effect) don't
  need an entry — the commit log already covers those.
- This complements the commit log; it does not replace it. Focus each entry on the "why"
  that a diff can't show.

## Entries

### 2026-09-06 — Python 3.13/3.14 support

- Raised the supported interpreter from `>=3.11,<3.12` to `>=3.13,!=3.14.1,<3.15`. The old
  bound was vestigial: it came in with the initial commit (as `>=3.10,<3.12`) and no
  dependency had required it for a long time. The library stack was already modern —
  pandas 3.0, numpy 2.3, scipy 1.17 — so the interpreter was the only stale part. The
  source needed no changes for the move: no `sys.version_info` branches, no imports of
  stdlib modules removed in 3.12/3.13, no `datetime.utcnow`, no numpy-1 aliases, and no
  `multiprocessing` (so 3.14's switch to a `forkserver` default is a non-issue).
- `3.14.1` is excluded explicitly because `networkx` declares `!=3.14.1` for that CPython
  patch release. Everything else in the tree is fine on both 3.13 and 3.14, and CI now runs
  a matrix over the two.
- **Replaced the `opentree` library with direct calls to the Open Tree of Life v3 REST
  API** in `TaxaEnhancer`. `opentree` 1.0.1 (last released 2021) publishes no wheel, so it
  is built from source on every install — and its `setup.py` falls through to
  `from distutils.core import setup`, a module removed from the stdlib in 3.12. It survived
  only on setuptools' deprecated `_distutils_hack` shim, which is on its way out, with no
  upstream maintainer left to fix it. Only two calls were ever used (`tnrs_match`,
  `taxon_info`), both thin JSON POSTs, and the enhancer already spoke `requests` for its
  Wikidata lookups. The replacement also fixes two problems the library had: it never set a
  request timeout (a wedged endpoint could stall a batch run forever) and it retained every
  response object in a `call_history` list for the process lifetime. Failures now raise
  `EnrichmentError` directly rather than surfacing indirectly through the missing-`results`
  guard. Dropping it also removed `dendropy` and a runtime `setuptools` dependency.
- Pruned dependencies that were declared but never imported: `spec2vec`, `memo-ms`,
  `plotly`, `scipy`, and (dev) `lz4`, `joblib`. `spec2vec` was the direct blocker for 3.14 —
  it declares `requires-python <3.14` because `gensim` ships no cp314 wheels — and
  `memo-ms` (untouched since 2022) pulled it back in along with `jupyter`, `ipykernel`,
  `scikit-bio`, `biom-format`, `cimcb-lite`, `bokeh` and `statsmodels` as *runtime*
  dependencies. The lock went from 181 packages to 80.
- Conversely, promoted five packages that the code imports but never declared — `numba`,
  `numpy`, `tqdm`, `psutil`, `matplotlib`. They had been surviving only as transitive
  dependencies of matchms, where a re-resolve could have silently dropped them.
- **`matchms` is deliberately held at `<0.21`.** Upgrading is a trap: 0.27+ pins
  `scipy<1.17` and `networkx<3.5`, which would drag the numeric stack *backwards*. Version
  0.20 leaves its own dependencies unbounded, and that is precisely what allows this project
  to run pandas 3 and scipy 1.17 today. `pyarrow` is kept despite having no import, because
  it backs DuckDB's zero-copy Arrow exchange with Polars in the loaders.
- Migrated `pyproject.toml` to PEP 621: dependencies and `requires-python` now live in
  `[project]`, and the Poetry-specific `packages` key moved to `[tool.poetry]` where it
  belongs. `poetry check` had been warning about the hybrid layout and is now clean.
- Left alone deliberately: ruff's `UP` (pyupgrade) rules stay off. The higher floor makes
  more of them applicable, but that is an annotation-style decision unrelated to the
  interpreter bump and would have swamped this diff.

### 2026-09-06

- Moved the five front-end-agnostic modules — `blocks`, `runner`, `batch_runner`,
  `config_io`, `log_utils` — out of `enpkg/monolith/gui/` into
  `enpkg/monolith/pipeline/`. None of them ever imported streamlit; only `app.py` and
  `form_builder.py` do. Keeping the registry and runners under a package named for the
  GUI meant the pipeline nominally depended on an *optional* dependency group, and it
  put the block registry — the pipeline's source of truth — behind a front-end. The
  boundary is now real: nothing under `pipeline/` may import streamlit.

- Blocks declare the shared resources they need via `BlockSpec.requires`
  (`"db_loader"`, `"lotus_store"`), and the runner builds the union of what the
  selected blocks asked for. Previously `build_shared_steps` decided this from a
  hard-coded `{"ms1", "ms2", "weights"}` id set, so any other block reading
  `ctx.lotus_store` silently got `None`. This also removes an asymmetry in the old
  code, where the weights path fell back to a default `MSEnhancerConfig` when none was
  supplied but the MS1/MS2 path left the loader unbuilt; all resource-needing blocks
  now take the same fallback.

- Split ordering out of `depends_on` into `BlockSpec.after` / `before`, and made
  `BLOCKS` the output of `order_blocks` (a topological sort of `_BUILTIN_BLOCKS`)
  rather than a hand-ordered list. `depends_on` answers "must this other block also be
  *selected*" and is checked against the selection; it never expressed *order*, which
  was carried implicitly by each entry's position in the list. That conflation is
  invisible while one hand-written list controls both, but it means a block's real
  ordering requirements are unstated and unenforced — and it is the thing that would
  block accepting blocks from anywhere but this file. Constraints now say what is
  true (`ms1_graph` before `ms1`; `weights` after `network`/`ms1`/`ms2`), and blocks
  nothing separates keep declaration order, so the computed sequence is unchanged and
  a test pins it.

- Added this changelog. Rationale, design decisions, and the history of how the pipeline
  got to its current shape now live here rather than in docstrings and code comments,
  which should only describe the code as it currently is.
