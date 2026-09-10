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

### 2026-09-10 — SIRIUS does not parallelise: four approaches measured and rejected

No code changed. This records why the planned SIRIUS parallelisation was abandoned,
so it is not re-proposed. Every number below is measured on this machine; the probe
scripts are not kept (they were scratch), but the method is described well enough to
repeat.

- **The premise.** Phase 0 measured SIRIUS at 88.6% of block time and ~85% of total
  experiment wall time. The plan was a worker pool running several SIRIUS processes
  at once, projected at 2.4x on two workers and 4.7x on four.
- **Concurrent processes are *slower*, not faster.** Three truncated samples
  (400 spectra each), cold cache both ways: **375.7s** run one at a time versus
  **617.3s** run three at once — 1.64x slower, each individual run degrading ~5x
  (114-133s alone, ~610s when sharing). They also changed the result: 10 of 1259
  rows (0.8%) got a different rank-1 structure, on InChIkey2D, smiles and
  CSI:FingerIDScore.
- **SIRIUS itself is deterministic**, which is what makes that 0.8% attributable:
  two sequential runs of the same input were identical on every science column
  across all 1259 rows.
- **Directory input is slower *and* corrupts attribution.** `--input <dir>` over the
  same three files took **600.6s** against 472.1s one-at-a-time. Worse, SIRIUS
  preserves the input feature ids rather than renumbering, and the summaries carry no
  column naming the source file — so 55 of 428 returned ids (13%) were ambiguous
  across samples and 14 ids came back with more than top-k rows, meaning two samples'
  features had merged under one id. `attach_sirius_annotations` joins on exactly that
  key. Namespacing ids before a merged run would fix the correctness half, but there
  is no speed to gain.
- **The searched-database list does not drive wall time.** Holding everything else
  fixed on one sample: 1 database 170.8s, 8 databases 129.3s, 25 databases (today's
  hardcoded list) 136.3s — flat, with one database the *slowest*. All three annotated
  the same 136 features, losing and gaining none. But the rank-1 structure changed for
  26% of features at 8 databases and 50% at one. So trimming the list buys nothing and
  changes a quarter to half of what gets ingested: **do not trim it**.
- **Why all four fail for one reason.** SIRIUS's own log reports a job manager fixed
  at 12 CPU and **4 IO threads**, and an HTTP pool at **MaxPerRoute=2-3 /
  maxTotal=5**; the dominant work unit is `FingerprintPreprocessingJJob`, the
  CSI:FingerID web path. CSI:FingerID issues **one request per feature**, and the
  database list only changes what the server searches inside that request — which is
  exactly why feature count matters and database count does not. The throttle looks
  server-side per account, since nine connections across three processes performed
  worse than three.
- **The pool is not tunable.** `de.unijena.bioinf.sirius.cpu.cores` and
  `.cpu.threads` (the only two such properties in the 6.3.4 jar) change the *reported*
  detection only — set either way, the job manager still initialises 12 CPU / 4 IO
  threads and MaxPerRoute=3. `-D` via `JDK_JAVA_OPTIONS` reaches the JVM but the
  property is read from the properties file, and even there it does not affect the
  allocation.
- **What is left, and it is modest.** Two levers survive, both worth roughly 15%:
  send SIRIUS fewer features (the MS1 adduct graph already classifies features as
  anchor / satellite / singleton, and `utils/ms2_adduct_gating.py` already applies
  exactly this reasoning for MS2 — a satellite is the same molecule as its cluster
  anchor); and overlap the single SIRIUS process with the pipeline's non-SIRIUS 15%.
  Note the feature-count saving is **not yet quantified for SIRIUS**: the adduct roles
  are stamped on `analysis.spectra` (1274 features for actea_EtOAc-1) while SIRIUS
  reads a separate `_sirius.mgf` with a different, larger feature set (2662), so the
  17.5% satellite share of the former does not transfer directly.
- **Conclusion.** SIRIUS wall time is an external web-service cost that cannot be
  parallelised away client-side. The worker pool, directory mode and database trimming
  are all rejected on measurement.

### 2026-09-10 — Per-stage timing instrumentation

- `RunResult` now carries `durations`, a map of wall-clock seconds per block id plus
  three reserved keys (`__load__`, `__rdf__`, `__pickle__`) for the work that happens
  outside the block loop. Every run reports it: a per-analysis `TIMING` section in the
  run summary, and an aggregate `TIMING BREAKDOWN` table — total, mean, share, and the
  number of experiments that recorded each stage — in `batch_summary.log`.
- The motivation was that nothing measured where pipeline time went, so proposals to
  speed it up rested on assumption. Scraping the `Running X …` / `X completed`
  timestamps out of an existing 12-experiment batch log
  (`gui_workspace/logs/batch_20260818_235853/`) gave the first answer: **SIRIUS is 88.6%
  of block time** (1139.5 s mean per experiment), weights 6.6%, and everything else
  under 5% combined. Per experiment, outside the block loop: load 1.1 s, RDF
  serialization 46.4 s, pickling the 1.5 GB `Analysis` 11.7 s. The whole batch was
  4.49 h for 12 experiments, SIRIUS 84.7% of it.
- That scrape is why the instrumentation exists rather than being a substitute for it.
  It only worked because block boundaries happen to be logged as progress messages, it
  saw nothing outside the block loop that was not separately logged, and repeating it
  meant re-writing the parser. The numbers are now an output of the run.
- Durations are recorded on the **failure** path as well as the success path. A block
  that runs for twenty minutes and then raises is the one whose duration is most worth
  reading, and the runner abandons the analysis as soon as a step raises, so a
  success-only timer would lose exactly that case.
- `time.perf_counter`, not `time.time`: it is monotonic, so a clock adjustment during a
  multi-hour batch cannot yield a negative duration.
- RDF serialization and pickling are absent from the *per-experiment* summary and
  present only in the batch table. Both are driven by the callers, after
  `_run_analysis` has already written the summary and closed its file handler. Rather
  than reorder that, each logs its own duration as a runtime line and the batch table
  collects them once every experiment has finished.
- Consequence worth recording for the parallelism work this was meant to inform: since
  SIRIUS is ~85% of total experiment time, overlapping it with the rest of the pipeline
  at one worker can save at most ~15%. Any larger gain requires running SIRIUS runs
  concurrently with each other, which is unverified — so the measurement moved that
  from a side assumption to the decisive question.

### 2026-09-07 — Integration-test fixtures

- Added `enpkg/scripts/build_test_fixtures.py`, which builds everything the
  integration suite needs from a full database you already have: a sampled
  DuckDB fixture and one dataset laid out the way `AnalysisLoader.from_files`
  expects. The suite previously depended on a ~1.7 GB production database plus
  several GB of Zenodo downloads that nothing in the repo produced, so the nine
  DB-backed tests could only run on a machine that happened to have them.
- The fixture is ~58 MB. Mass-filtering alone does not shrink this database —
  the dataset's precursors span m/z 81-822 and reach 201k of the 220k compounds —
  so the sample is taken by taxonomy instead: every compound of the dataset's own
  genus (so taxonomically weighted scoring keeps real signal), then a slice of the
  surrounding family and a slice of everything else. Selection is ordered by
  `hash()` of the row key rather than seeded randomness, so a given source
  database always yields a byte-identical fixture.
- `_meta_columns` is copied whole. Its rows name the columns behind the pathway,
  superclass and class probability arrays (7 + 77 + 696), so a partial copy would
  silently misalign every score rather than fail.
- The fixture database is written as `enpkg_fixture.duckdb`, not `enpkg.duckdb`,
  so building it can never overwrite a full database sitting in the same
  directory.
- Test fixture paths are now resolved from the test package's own location rather
  than from `$PROJECT_ROOT`. Honouring the environment variable meant a `.env`
  copied between machines silently redirected fixtures to a path outside the two
  gitignored directories — on this machine it had scattered 6.1 GB of downloads
  into an untracked `tests/` at the repository root.
- Fixed three test fixtures that had gone stale against the uniform
  `enhance(analysis) -> Analysis` contract: two called
  `TaxaEnhancer.enhance(genus, species)`, which has taken a single `Analysis`
  since the enhancer contract was unified, and both then assigned the *analysis*
  returned by `NetworkEnhancer.enhance` to a `molecular_network` field. They now
  chain the two enhancers. These failures were invisible because the tests
  errored earlier on the missing database.
- Rewrote the SIRIUS argv assertion, which fixing the above unmasked. It compared
  the command line to one literal list, but the implementation resolves `--input`
  to an absolute path and writes the project under a run-timestamped directory —
  so the assertion could not hold on Windows or across runs. It now checks the
  argv structurally: the resolved input path, the project file's name and parent,
  the configuration flags as a set, and that the tool subcommands appear in order.

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
