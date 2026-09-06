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
