# ENPKG Full — Refactoring, Bug-Fix, Test & Documentation Plan

> Prepared for hand-off to an implementer. Scope: whole `enpkg/monolith` package plus
> `enpkg/scripts` and `enpkg/tests`. Goal: simplify, remove stale/redundant code, finish
> half-built parts, surface bugs, bring docs up to date, and lay out the missing tests.
> Guiding principle throughout: **less code is better**, and the as-built architecture
> (data-model → enhancers → pipeline steps → GUI runner/registry → RDF serializer) should
> stay understandable and be made internally consistent.

---

## 0. Architecture as-built (orient here first)

The live data flow is:

```
AnalysisLoader.from_files ─► Analysis (pydantic)
   Analysis ▸ SampleMetadata, ▸ tuple[AnnotatedSpectrum], ▸ ott_matches, ▸ molecular_network
AnnotatedSpectrum (subclasses matchms.Spectrum) ▸ ms1_annotations[ChemicalAdduct]
                                                ▸ ms2_annotations[MS2ChemicalAnnotation]
                                                ▸ ms1_/ms2_ pathway/superclass/class scores

Enhancers (compute)     Pipeline steps (adapt+wire)    GUI (drive)            Output
────────────────────    ───────────────────────────    ─────────────────     ─────────────
TaxaEnhancer            TaxonomicalEnhancementStep      blocks.py (registry)  rdf/serializer.py
NetworkEnhancer         MolecularNetworkingStep         runner.py (single)    → Turtle
MS1Enhancer             MS1EnhancementStep              batch_runner.py (many)
Ms2Enhancer             MS2EnrichmentStep               app.py (Streamlit)
WeightsEnhancer         WeightsEnhancementStep          form_builder/config_io
SiriusEnhancer          SiriusEnhancementStep

Data backing: LotusStore + DBLoader → DatabaseManager (DuckDB: compounds, npc_classifications,
spectral_library). Build once with scripts/build_duckdb.py.
```

**The single most important structural fact:** the real orchestrator is the GUI
`runner.py` + `blocks.py` registry + `pipeline/*_step.py`. The `pipeline/pipeline.py` +
`pipeline/default_pipeline.py` + `data/batch_class.py` cluster is the **old** abstraction and
is dead (see §2). Any refactor should consolidate on the runner/registry model that
`gui/ARCHITECTURE.md` already documents.

---

## 1. Bugs (prioritized)

### P0 — blocking / currently broken

- **B-01  MS2 enhancer/step/test signature mismatch (crashes).**
  `Ms2Enhancer.enhance(self, analysis, chunk_size)` reads `analysis.spectra`
  ([ms2_enhancer.py:164](../enpkg/monolith/enhancers/ms2_enhancer.py#L164)), but it is called
  with a **tuple of spectra**, not an `Analysis`, from both
  [ms2_enhancement_step.py:43](../enpkg/monolith/pipeline/ms2_enhancement_step.py#L43)
  (`enhancer.enhance(analysis.spectra)`) and
  [test_ms2_enhancer.py:81](../enpkg/tests/test_enhancers/test_ms2_enhancer.py#L81). Passing a
  tuple → `AttributeError: 'tuple' object has no attribute 'spectra'`. The step then does
  `analysis.model_copy(update={"spectra": enriched})` while `enhance` returns the whole
  `Analysis` — a second inconsistency.
  **Fix (decide one contract, apply everywhere):** make MS2 mirror MS1 — signature
  `enhance(self, spectrum_list, chunk_size=1000) -> list[AnnotatedSpectrum]`, mutate in place,
  return the list; the step wraps with `model_copy(update={"spectra": ...})`. (Or the inverse:
  make *both* MS1 and MS2 take and return `Analysis`. Pick one — see B-07 / §3.1.) Update the
  test to match.

- **B-02  DuckDB build script is broken.**
  [build_duckdb.py:100](../enpkg/scripts/build_duckdb.py#L100) calls
  `db.import_from_csvs(metadata_path=args.metadata, ...)` but the parameter is
  `lotus_metadata_path` ([database_manager.py:202](../enpkg/monolith/loaders/database_manager.py#L202)).
  → `TypeError`. Nobody can build the DB from this entrypoint.
  **Fix:** rename the kwarg to `lotus_metadata_path` (or rename the method parameter to
  `metadata_path`; pick one and keep it consistent with the CLI flag name).

- **B-03  Old pipeline cluster is import-broken.**
  [pipeline/pipeline.py:50](../enpkg/monolith/pipeline/pipeline.py#L50) calls
  `enhancer.enrich(analysis)` — no such method (it's `enhance`); `self.enhancers` is used as an
  attribute while the ABC declares `enhancers()` as a method (name collision);
  `Pipeline.__init__` requires `enhancers` but `DefaultPipeline.__init__` calls
  `super().__init__()` with none. [default_pipeline.py:15](../enpkg/monolith/pipeline/default_pipeline.py#L15)
  imports a non-existent `isdb_enhancer.ISDBEnhancer`. This whole cluster is dead — **delete it**
  (see §2) rather than fix it.

### P1 — high (wrong results / silent no-ops)

- **B-04  MS1 upper-bound scan can `IndexError`.**
  [ms1_enhancer.py:203](../enpkg/monolith/enhancers/ms1_enhancer.py#L203):
  `while upper_mass_bound > self._adducts[upper_mass_bound_index].adduct_mass:` accesses the
  array **before** the `== len(...)` guard. If the precursor is heavier than every adduct,
  `lower_mass_bound_index == len(self._adducts)` and the first access is out of range.
  **Fix:** replace the linear scan with `bisect_right` on the sorted adduct-mass key
  (`upper_index = bisect_right(...)`), which is simpler, faster, and bounds-safe. This also
  removes the hand-rolled loop.

- **B-05  MS2 negative mode never loads the neg library.**
  [ms2_enhancer.py:67](../enpkg/monolith/enhancers/ms2_enhancer.py#L67) hardcodes
  `load_spectral_databases(mode="pos")` (TODO acknowledged). In neg polarity the pos library is
  used. **Fix:** pass `self.configuration.general_params.polarity`.

- **B-06  MS2 peak-count threshold is strict `>`.**
  [ms2_enhancer.py:233-235](../enpkg/monolith/enhancers/ms2_enhancer.py#L233): `n_matches >
  min_peaks` requires **≥7** matches when `min_peaks=6`. `min_peaks` reads as an inclusive
  minimum everywhere else (config help: "Minimum number of matching peaks required"). **Fix:**
  use `n_matches >= min_peaks` (and confirm the intended boundary for `min_score` — likely
  keep `>` or make `>=`, but be explicit and comment it).

- **B-07  `Enhancer` ABC is not honoured — signatures diverge.**
  `Enhancer.enhance(self, analysis: Analysis) -> Analysis`
  ([enhancer.py:11](../enpkg/monolith/enhancers/enhancer.py#L11)) but:
  `MS1Enhancer.enhance(spectrum_list) -> list`, `Ms2Enhancer.enhance(analysis) -> Analysis`
  (broken, B-01), `NetworkEnhancer.enhance(analysis) -> nx.Graph`,
  `TaxaEnhancer.enhance(genus, species) -> list[Match]`. The ABC gives false guarantees.
  **Fix (chosen — see E-03 / R-08):** commit to a uniform
  `enhance(self, analysis: Analysis) -> Analysis` across all enhancers (each reads what it needs
  off `analysis` and returns an updated instance), and resolve the repeated
  `# TODO: modify in place vs return new` comments to one rule (return an updated copy). This
  uniform contract is what lets the step layer collapse onto the registry (R-08). Making MS1 and
  MS2 identical is the first move and also clears B-01.

- **B-08  Reweighting config knobs are inert.**
  `msms_weight`, `taxo_weight`, `chemo_weight`, `top_N_chemical_consistency`,
  `min_score_taxo_ms1`, `min_score_chemo_ms1`, `use_post_taxo`, `top_to_output`
  ([reweighting_config.py:8-49](../enpkg/monolith/configuration/reweighting_config.py#L8)) are
  defined and validated but **never read** by `WeightsEnhancer`. Anyone tuning them sees no
  effect. **Fix:** either wire them into the weighting/ranking (the WEIGHTS_ENHANCER.md doc
  says the final blend "happens downstream at output time" — so they belong in the serializer's
  ranking, `_alignment_score`/`_add_ranked_annotations`), or delete the unused fields until the
  feature exists. Do not leave dead knobs.

### P2 — medium (latent / correctness-adjacent)

- **B-09  Duplicate `ott_matches` field on `Analysis`.**
  [analysis.py:28](../enpkg/monolith/data/analysis.py#L28) and
  [:30](../enpkg/monolith/data/analysis.py#L30) both declare `ott_matches` (the second,
  `Optional[...]`, shadows the first). Keep exactly one.

- **B-10  `TaxonomicalEnhancementStep` uses `self.logger` it never sets.**
  [taxonomical_enhancement_step.py:14](../enpkg/monolith/pipeline/taxonomical_enhancement_step.py#L14)
  calls `self.logger.info` but the class has no `__init__` setting it, and the runner builds it
  via `cls()`. Currently masked because `can_run` gates out the only branch that logs, but
  `process()` is unsafe to call directly. **Fix:** give the step a logger (constructor or module
  logger) consistent with the other steps.

- **B-11  MS1 tolerance is Daltons, not ppm.**
  MS1 precursor window uses `parent_mz_tol` (Da). The historical workflow used a ppm tolerance
  for MS1 annotation. This changes *which* adducts match. **Fix:** confirm intended; if ppm is
  wanted, add a ppm option to `SpectralMatchParams` and use it for the MS1 window. At minimum
  document the choice.

- **B-12  `sirius_enhancer.py` accidental import + `print`.**
  [sirius_enhancer.py:7](../enpkg/monolith/enhancers/sirius_enhancer.py#L7)
  `from tabnanny import check` is dead/accidental; [line 91](../enpkg/monolith/enhancers/sirius_enhancer.py#L91)
  uses `print()` instead of the logger. Remove both.

---

## 2. Dead / stale code to remove (biggest simplification win)

None of the following are imported by the live GUI/runner/step/RDF path (verified by grep).
Deleting them removes a large amount of confusing, broken, or duplicated code.

| Path | Why it's dead | Action |
|---|---|---|
| [pipeline/pipeline.py](../enpkg/monolith/pipeline/pipeline.py) | Old `Pipeline` ABC, calls `enrich()`, bare `monolith.` imports, superseded by runner | **Delete** |
| [pipeline/default_pipeline.py](../enpkg/monolith/pipeline/default_pipeline.py) | Imports non-existent `isdb_enhancer`; broken `super().__init__()`; superseded | **Delete** |
| [data/batch_class.py](../enpkg/monolith/data/batch_class.py) | Old `Batch`, imports non-existent `analysis_class`, old Analysis API | **Delete** — but first port its `peak_processing` + `require_minimum_number_of_peaks` into `AnalysisLoader` (see §4) |
| [data/analysis_class_LCA.py](../enpkg/monolith/data/analysis_class_LCA.py) | Old pandas `Analysis`, bare `monolith.` imports | **Delete** — but port `normalized_source_taxon` normalisation and `sample_type` sample/blank handling (see §4) |
| [output/analysis_export.py](../enpkg/monolith/output/analysis_export.py) | `ResultExporter`/`quicksort` reference the removed MS2 `.scores["cosine_hungarian"]` shape; RDF serializer is the real export | **Delete** (fold any useful bit into a serializer smoke test) |
| [data/ms1_data_classes/ms1_configuration_class.py](../enpkg/monolith/data/ms1_data_classes/ms1_configuration_class.py) | `MS1EnhancerConfig` superseded by `MSEnhancerConfig`; only referenced by dead code + `__main__` | **Delete**; drop it from `ms1_data_classes/__init__.py` `__all__` (keep `ChemicalAdduct`, `AdductRecipe`) |
| [data/isdb_data_classes/](../enpkg/monolith/data/isdb_data_classes/) | Only referenced in commented code | **Delete package** |
| [data/sirius_data_classes/](../enpkg/monolith/data/sirius_data_classes/) | Only referenced in commented code; Sirius not wired to the model yet | **Delete** (re-add when Sirius ingestion lands, §4) |
| [data/spectral_network_classes/](../enpkg/monolith/data/spectral_network_classes/) | Only in commented `data/__init__.py`; `NetworkEnhancerConfig` lives in `configuration/` | **Delete package** |
| [utils/memlog.py](../enpkg/monolith/utils/memlog.py) | `log_virtual_memory` never imported; duplicates `dev_utils.log_memory_snapshot` | **Delete** (keep one memory helper — see §3) |
| [utils/benchmark_builder.py](../enpkg/monolith/utils/benchmark_builder.py) | One-off data-prep script, uses matplotlib (not a dependency), imported nowhere | **Move to `enpkg/scripts/`** (or delete) |
| [rdf/Untitled.ipynb](../enpkg/monolith/rdf/Untitled.ipynb) | Scratch notebook | **Delete**, add `*.ipynb` to `.gitignore` |
| [pipeline/test_pol.py](../enpkg/monolith/pipeline/test_pol.py) | Manual dev driver, hardcoded `/home/llegregam/...` paths | **Convert** to a proper integration test (guarded by a DB-available marker) or move to `scripts/`; do not leave in `pipeline/` |
| [data/__init__.py](../enpkg/monolith/data/__init__.py) | Entirely commented-out (bare `monolith.` imports) | **Replace** with real re-exports or leave minimal; don't ship a file of comments |

Also remove the dead scaffolding embedded in live files:

- `__main__` blocks with hardcoded absolute paths in
  [ms1_enhancer.py:218-289](../enpkg/monolith/enhancers/ms1_enhancer.py#L218),
  [ms2_enhancer.py:294-325](../enpkg/monolith/enhancers/ms2_enhancer.py#L294),
  [sirius_enhancer.py:208-268](../enpkg/monolith/enhancers/sirius_enhancer.py#L208),
  [ms2_enhancement_step.py:53-86](../enpkg/monolith/pipeline/ms2_enhancement_step.py#L53). Delete;
  their role belongs to `enpkg/tests`.
- Large commented-out blocks: the LOTUS-cache section in `ms1_enhancer.py`, the debug sampler in
  `ms2_enhancer.py:273-289`, the `_reconstruct`/computed-field comments in
  `sirius_enhancer_config.py`, `tops_present` in `rdf/enhancer_parsers.py:90-99`.
- Move [rdf/enhancer_parsers.py](../enpkg/monolith/rdf/enhancer_parsers.py) (the real
  `SiriusOutputParser`) out of `rdf/` — it parses SIRIUS TSVs and has nothing to do with RDF.
  Put it under `enhancers/` or a new `parsers/` module; it will be needed when Sirius ingestion
  is wired (§4). Note it duplicates the `SiriusResults` name with the empty stub in
  `sirius_enhancer.py` — collapse to one.
- The `.owl` vocab files (`EMI-vocab.owl`, `chebi-vocab.owl`, `ms-voab.owl` [sic — typo in
  filename], `NCBITaxon_slim-vocab.owl`) are reference vocabularies. Keep, but relocate to
  `docs/vocab/` or `rdf/vocab/` and reference from the RDF docs; fix the `ms-voab.owl` filename
  typo.

---

## 3. Refactoring / simplification (DRY, keep behaviour)

- **R-01  Collapse the four giant duplicated SELECTs in `DatabaseManager`.**
  `get_all_compound_metadata`, `get_compound_metadata_sorted_by_short_inchikey`,
  `get_compound_metadata_by_formulas`, `get_compound_metadata_by_mass_range`
  ([database_manager.py:551-708](../enpkg/monolith/loaders/database_manager.py#L551)) repeat the
  same ~35-column `SELECT ... LEFT JOIN npc_classifications` four times. Extract one
  `_COMPOUND_SELECT` constant and append only the `ORDER BY` / `WHERE`. Removes ~100 lines.
  While there, **audit for unused queries**: `get_all_compound_metadata`,
  `get_compound_metadata_by_formulas`, and `get_spectra_by_mass_range` have no production caller
  (LotusStore uses only the sorted-by-short-inchikey and by-mass-range paths). Delete the ones
  with no test/consumer, or add a test that pins them if intentionally kept.

- **R-02  Centralise the taxonomy rank-ladder (defined 3×).**
  The 8→0 rank ladder is implemented in `Lotus.taxonomical_similarity_with_otl_match`
  ([lotus_class.py:200](../enpkg/monolith/data/lotus_class.py#L200)) and
  `AnnotationOrganism.taxonomical_similarity_with_match`
  ([chemical_annotation.py:35](../enpkg/monolith/data/chemical_annotation.py#L35)); `Match`
  exposes the matching rank properties. And `MAXIMAL_TAXONOMICAL_SCORE = 8.0` is defined in
  **two** modules. Extract one free function `taxonomical_similarity(a, b) -> float` (operating
  on a small `TaxonLike` protocol with `.domain … .species`) plus one `MAXIMAL_TAXONOMICAL_SCORE`
  constant, and call it from both. Removes a whole duplicated ladder.

- **R-03  De-duplicate the LPA blocks in `WeightsEnhancer.enhance`.**
  The MS1 and MS2 halves each run three near-identical `label_propagation_algorithm` calls with
  their own tqdm bar ([weights_enhancer.py:199-282](../enpkg/monolith/enhancers/weights_enhancer.py#L199)).
  Extract `_propagate_triplet(analysis, (pathway, superclass, class)) -> (…)`; call it twice.
  Also factor the shared skeleton of `compute_ms1_classifications` /
  `compute_ms2_classifications` if it stays readable (they differ mainly in the MS2 organism
  filter + cosine weight). Net: ~60 lines → ~25.

- **R-04  One memory-snapshot helper.** Keep `dev_utils.log_memory_snapshot` (it's the one
  used by `batch_runner`), delete `utils/memlog.py` (§2). If the lighter one-line format is
  wanted, make it a `level`/`compact` flag on the surviving function.

- **R-05  `AnnotatedSpectrum` stores `mass_over_charge` redundantly.**
  The constructor stores `self.mass_over_charge` *and* exposes `precursor_mz` (both read the same
  metadata) and asserts they agree ([annotated_spectra_class.py:35-40](../enpkg/monolith/data/annotated_spectra_class.py#L35)).
  Consider dropping the separate attribute and the guard, or documenting why both exist. Minor,
  but it's confusing state.

- **R-06  `Urls`/`Paths` config duplication.** `Urls` and `Paths`
  ([MSEnhancer_config.py:10-119](../enpkg/monolith/configuration/MSEnhancer_config.py#L10)) each
  reimplement `__iter__`/`items`/`__len__`/`empty` over the same field set. A tiny shared mixin
  (iterate non-empty `model_fields`) removes the repetition. Low priority.

- **R-07  Remove the now-unused `binary_search` linear tail in MS1** once B-04 switches to
  `bisect`. If `binary_search_by_key` then has no remaining caller, delete it too (and its test)
  — check first; it may still be the lower-bound finder.

- **R-08  Collapse the step layer onto the registry (accepted — E-03; depends on B-07).**
  Once every enhancer honours the uniform `enhance(analysis) -> analysis` contract, each
  `pipeline/*_step.py` is ~15 lines of boilerplate (build enhancer → `can_run` → `enhance` →
  wrap) around a single call. Move that data onto each `BlockSpec` in `blocks.py`: a `build(...)`
  callable (or the enhancer class plus the names it needs from the shared
  `{db_loader, lotus_store, logger, config}` set) and an optional `can_run` predicate.
  `_run_analysis` then becomes `enhancer = spec.build(...); if spec.can_run(analysis): analysis =
  enhancer.enhance(analysis)`. The one step with extra behaviour —
  `MolecularNetworkingStep.export_components` — stays as a small function or registry hook.
  Expected deletion: ~5 of the 6 `pipeline/*_step.py` files. Do this **after** the T3 step tests
  exist so behaviour is pinned before the move; the `blocks.py` registry stays the single source
  of truth, as `gui/ARCHITECTURE.md` already advocates.

---

## 4. Incomplete features to finish (add to backlog)

- **F-01  Spectra cleaning / min-peaks filter in `AnalysisLoader`.**
  The audit flagged `require_minimum_number_of_peaks` as missing; it (and
  `default_filters`/`normalize_intensities`/`select_by_mz`/`select_by_intensity`) live only in
  the dead `batch_class.peak_processing`. Port a configurable cleaning step into
  `AnalysisLoader._load_spectra` (or a new `_clean_spectrum`) before building `AnnotatedSpectrum`,
  driven by config. This is real science parity, not just cleanup.

- **F-02  Sample-type QC/blank exclusion.** `SampleMetadata.sample_type` exists but nothing
  gates on it; the old code enforced `sample_type == 'sample'`. Decide the policy (filter at load,
  or a `can_run`/skip in the runner) and wire it. Port `normalized_source_taxon` string
  normalisation (`" sp. "`, `" x "` handling) from `analysis_class_LCA` into `SampleMetadata` or
  `Analysis.genus_and_species`.

- **F-03  Finish SIRIUS ingestion.** Today `SiriusEnhancer._get_results` and
  `SiriusResults.from_path` are `pass`; `SiriusOutputParser` (rdf/enhancer_parsers.py) is written
  but never called; `AnnotatedSpectrum` has SIRIUS annotations commented out; `_log_sirius` is a
  stub. Wire: run → `SiriusOutputParser.digest_paths` → attach `SiriusChemicalAnnotation` to
  spectra → summarise in `_log_sirius` → serialize in RDF. Or, if out of scope now, explicitly
  document SIRIUS as "runs, output not yet ingested" and remove the half-built stubs.

- **F-04  MS2→MS1 adduct coupling** — implement
  [docs/MS2_MS1_ADDUCT_COUPLING_PLAN.md](MS2_MS1_ADDUCT_COUPLING_PLAN.md) (planned, not built).
  Self-contained, serializer-only, already has a verification recipe.

- **F-05  Wire RDF export into the runner/batch.** The serializer is solid but only invoked by
  the manual `rdf/smoke_serialize.py`. Add an export option/step so a run/batch can emit `.ttl`
  directly (e.g. an `export` block, or a `--serialize` flag on `batch_runner`). Settle the
  `ENPKG` namespace IRI TODO ([namespaces.py:19](../enpkg/monolith/rdf/namespaces.py#L19)).

- **F-06  Provenance in RDF.** Record code version (git sha / `__version__`) and DB versions
  (`queried_against="ISDB"` has a TODO for versioning). Add `prov:wasGeneratedBy` with a
  software-agent node. Old workflow wrote a commit hash; this restores that.

- **F-07  `recompute` skip.** `general_params.recompute` is defined but no step caches/skips on
  it. Either implement a skip-if-exists path or drop the field.

---

## 5. Optimizations

- **O-01  MS1 bound-finding → `bisect`** (see B-04). Removes the loop and the bug at once.
- **O-02  MS2 stage-2 loop is the hot path.** The per-pair Python loop over
  `cosine_similarity.pair(...)` ([ms2_enhancer.py:223-231](../enpkg/monolith/enhancers/ms2_enhancer.py#L223))
  dominates runtime. Explore `matchms.calculate_scores(..., is_symmetric=False)` with the
  precursor mask to vectorise, or at least hoist invariant lookups out of the loop. Measure
  before/after on the toy dataset.
- **O-03  Serializer dedup set grows unbounded across a batch.** `self._emitted`
  ([serializer.py:101](../enpkg/monolith/rdf/serializer.py#L101)) accumulates run-scoped URIs
  (spectra/adducts/ms2ann) that never recur across analyses. For very large batches, clear the
  run-scoped entries per `add_analysis` while keeping the truly shared ones (compounds,
  organisms, recipes). Minor unless batches are huge.
- **O-04  `LotusStore` canonical list + formula groups** already cache well; just add a one-line
  note that `all_sorted_by_short_inchikey()` returns a shared, must-not-mutate list (it says so;
  keep it).

---

## 6. Test plan (what to add, prioritized)

Current suite (`enpkg/tests/test_enhancers/`) is mostly **integration** tests that need the real
DuckDB and network (OTT/Wikidata). There are **no** tests for the RDF layer, the loaders' pure
logic, the data-model validators, or the GUI pure functions. Priorities:

### T1 — pure-unit, no external deps (fast, high value)

- **RDF `uris.py`** — `analysis/spectrum/adduct/recipe/ms2ann/ott_match` minting; `_recipe_hash`
  order-independence; the adduct key's **two** discriminators (recipe hash *and* formula group —
  same recipe + different formula must not collide) and `_formula_segment`'s encoding/fallback
  cascade; `_organism_uri` cascade (wikidata → OTT → None); `lotus_uri` cascade
  (inchikey → wikidata → smiles-hash); `_short_hash` stability.
- **RDF `serializer.py`** — build a tiny fixture `Analysis` (1 spectrum, 1 adduct, 1 Lotus, 1
  MS2 annotation, 1 Match) and assert: analysis node type/predicates; compound `INCHIKEY:` URI;
  **dedup** (calling `add_analysis` twice adds no duplicate compound triples); `top_k_ms1/ms2`
  capping + `annotationRank`; `_format_adduct` strings (`[M+H]+`, `[2M+Na]+`, `[M+2K-H]+`,
  neg/charge cases); `_add_reference` DOI percent-encoding; `_set` skips None/NaN/empty;
  Turtle round-trip (serialize → parse → triple count).
- **`binary_search_by_key`** — found / not-found insertion point / empty array / bounds; the
  precursor-heavier-than-all case that triggers B-04.
- **`AdductRecipe` / `ChemicalAdduct`** — `compute_adduct_mass` (multimer, charge, negative
  counts); `validate_lotus` rejects mixed molecular formulas and empty lists; `short_inchikey`,
  `molecular_formula`, `positive` derivations.
- **`SampleMetadata`** — `from_dict` known/extra split; `normalize_source_taxon` (`nd`/`nan`/`""`
  → None); NaN coercion.
- **`Analysis` validators** — `feature_ids` (raises on missing); `validate_network_integrity`
  (node count / set / order mismatches); `genus_and_species` (no-taxon, no-space);
  `best_ott_matches` empty.
- **`AnnotatedSpectrum`** — precursor-mismatch guard; `polarity`; `has_ms1/ms2_annotations`;
  `get_top_k_lotus_annotation` / `get_top_k_ms2_structures` ranking with hand-set score vectors.
- **Config validators** — `NetworkEnhancerConfig` (top_n > max_links); `ReweightingParams`
  (weights-sum-zero raises); `GeneralParams` polarity regex; `EnhancerConfig` `extra='forbid'`.
- **Taxonomy ladder** (post R-02) — species→domain rungs, no-shared-rank → 0, normalisation.

### T2 — loader / DB logic (small fixtures)

- **`AnalysisLoader`** — separator sniffing (comma/semicolon/tab, and the "no separator" raise);
  `_load_quantification_table` missing-column error + `ignore_errors` fallback; quant `row ID`
  lookup + missing-feature_id `ValueError`; `_load_metadata` raw-extension matching + not-found
  raise. Use tiny in-repo CSV/MGF fixtures (the toy dataset already exists).
- **`DBLoader`** path logic — `_derive_path_from_url` (query strings, url-encoding, empty),
  `_reconcile_extracted_paths` (`.gz` → extracted), `_validate_redownload_fields`. No network:
  point at temp files.
- **`DatabaseManager`** — build an in-memory/temp DuckDB from a 3-row CSV set; assert
  `import_from_csvs` row counts, `_meta_columns` persistence, mass-range/short-inchikey queries,
  spectral round-trip. This also guards R-01 and B-02.

### T3 — pipeline / GUI pure functions

- **Runner/registry** — `BLOCKS` ordering + `depends_on` (weights needs network); `_run_analysis`
  dependency-skip and `can_run`-skip paths (with stub steps); `build_shared_steps` builds one
  `DBLoader`/`LotusStore` for MS1/MS2/weights.
- **`batch_runner`** — `discover_experiments` (skips folders missing spectra/quant, picks up
  `_sirius` sibling), `find_shared_metadata` preference order, `_sirius_config_for` path
  rewriting.
- **`config_io`** — YAML round-trip (`save_unified_yaml` → `load_unified_yaml`), MS1/MS2 shared
  `ms_enhancer` key mapping, `build_configs` raises on invalid section.
- **Steps** — after B-01/B-07, a test that each `*Step.process` returns an `Analysis` and
  `can_run` gates correctly, using a fake enhancer to stay fast.

### T4 — mark/skip the heavy integration tests

Give the DB/network-dependent tests (`test_ms1/ms2/weighting/taxa/network/lotus_store/
database_manager`) a `@pytest.mark.integration` (or `requires_db`/`requires_network`) marker and
register it in `pytest.ini`, so `pytest -m "not integration"` runs the fast suite in CI. Right
now `pytest.ini` only sets logging; add markers + a default addopts.

---

## 7. Documentation plan

### Update (stale)

- **README.md** — describes a workflow that no longer exists: `sh workflow/00_workflow_all.sh`,
  `extract_sha.py`, `merge_sha_metadata.py`, `params/user.yml`. There is **no `workflow/` dir**.
  Rewrite around the real entrypoints: build the DuckDB (`python -m enpkg.scripts.build_duckdb`),
  run the Streamlit GUI (`streamlit run enpkg/monolith/gui/app.py`), single vs batch runs, and
  RDF/Turtle output. Keep the science intro + citations.
- **gui/ARCHITECTURE.md** — mostly accurate, but references
  `enpkg/monolith/pipeline/test.py` (now `test_pol.py`, and slated to move, §2). Fix the two
  references. Consider renaming/promoting it to a top-level `docs/ARCHITECTURE.md` that also
  covers the non-GUI layers (see below).
- **Audit_08062026.md** — good historical audit; annotate which items are now
  fixed/changed (B1–B6) so it isn't mistaken for current state, or fold its live findings into
  this plan and archive it.
- **RDF_SERIALIZATION_PLAN.md / RDF_DATA_MODEL.md** — early planning docs using a placeholder
  namespace (`enpkg.example.org`) that differs from the implemented `w3id.org/emi` + `w3id.org/
  enpkg`. Mark both **superseded by `RDF_DATA_MODEL_mapped.md`**, or update the namespaces so a
  reader isn't misled.
- **loaders/DB_Project_build_schema.md** and **loaders/plan.md** — verify against the current
  `DatabaseManager` schema (esp. after R-01) and the `build_duckdb.py` fix (B-02).

### Accurate — leave as-is (verified against code)

`docs/MS1_ENHANCER.md`, `docs/MS2_ENHANCER.md`, `docs/WEIGHTS_ENHANCER.md` are high-quality and
correct — MS1_ENHANCER.md even flags the LPA-import bug (B-07/B-12) as a "common misconception".
After fixing that code, update the note in MS1_ENHANCER.md to past tense.

### Write (missing, high value)

- **`docs/ARCHITECTURE.md`** (whole system, not just GUI): the data model
  (`Analysis ▸ AnnotatedSpectrum ▸ adducts/annotations/scores`), the enhancer-vs-step-vs-
  block-vs-runner layering and *why* it's split that way, the `LotusStore`/`DBLoader`/
  `DatabaseManager` DuckDB story, and the RDF layer. This is the single most useful doc for
  making the system understandable to a newcomer.
- **`docs/DATA_PIPELINE.md`** or a README section: how to obtain the LOTUS CSVs + ISDB pickle and
  build the DuckDB, end to end (the current knowledge is spread across config defaults and the
  build script).

---

## 8. Missing docstrings / comments worth adding

- **`enhancer.py`** — document the real contract once it's settled (B-07): which enhancers take
  `Analysis` vs a spectra list, and the in-place-vs-copy rule. Right now the ABC docstring
  actively misleads.
- **`ms1_enhancer.py:3`** — module docstring says the enhancer "computes its LPA scores"; it does
  not (LPA is in Weights). Fix to match MS1_ENHANCER.md.
- **`weights_enhancer.py`** — no module docstring and no class-level explanation of the
  taxonomy-weight → NPC-feature → LPA flow; add a short one pointing to WEIGHTS_ENHANCER.md.
  Document why `compute_ms2_classifications` filters on `has_organisms()`.
- **`numba_label_propagation`** ([label_propagation_algorithm.py:9](../enpkg/monolith/utils/label_propagation_algorithm.py#L9))
  — the self-loop weighting (`weights_sum += 1`) and the `zeroed_mask` in-place mutation across
  iterations are subtle; add 2–3 comments so the dense math is followable.
- **`serializer._alignment_score`** — one line on the "mean(candidate·propagated) product" being
  the same score `get_top_k_*` rank by, and why `-inf` on mismatch.
- **Config fields currently unused (B-08)** — until wired, annotate the inert `ReweightingParams`
  fields as "reserved / not yet consumed" so users don't expect an effect.
- **`Analysis.feature_ids`** returns `int`s from metadata though typed `list[str]`; align the
  annotation and note that network node identity depends on it.

---

## 9. Extra ideas

### 9a. Accepted — now in scope

Floated originally as options; **accepted and in scope**. Treat as planned work and fit them
into the sequencing (§10). Cross-referenced from the numbered work items where they land.

- **E-01  Adopt `ruff` + a light pre-commit + a CI job.** Add `ruff` (lint + `--fix`
  import-pruning) and a minimal `.pre-commit-config.yaml`; add a CI workflow (e.g. GitHub
  Actions) that runs `ruff check` and `pytest -m "not integration"` on push/PR. Most of the dead
  imports, stray `print`s, duplicate imports (e.g. the double `POSITIVE/NEGATIVE_RECIPES` import
  in `ms1_enhancer.py`), and unused names catalogued in §1–§2 would be caught automatically and
  stay caught — the cheapest way to keep "less code" from regressing. Do this **after** the §2
  deletions and §3 DRY passes so the first run is near-clean; add a `[tool.ruff]` section to
  `pyproject.toml` tuned to the codebase (line length, and an ignore for the numba kernel in
  `label_propagation_algorithm.py`). This CI job is also the delivery vehicle for the fast unit
  tier (§6, T4).

- **E-02  `--neg` end-to-end smoke path.** After B-05 (neg-library fix), add a small
  negative-mode fixture (a neg `.mgf` + quant table) and a neg spectral library in the test
  DuckDB, plus an integration test that runs MS2 — and ideally the full block chain — in `neg`
  polarity. Keeps both polarities exercised and pins B-05. Lives in the T4 integration tier
  (§6), marked `@pytest.mark.integration`.

- **E-03  Uniform `Enhancer` contract keyed on `Analysis`, then collapse the step layer.**
  This is the chosen resolution for B-07: commit to a single
  `Enhancer.enhance(self, analysis: Analysis) -> Analysis` signature (each enhancer reads what it
  needs off `analysis` and returns an updated instance). Once every enhancer honours it, the
  `pipeline/*_step.py` classes are ~15 lines of boilerplate each and fold into data-driven
  `BlockSpec` entries in `blocks.py` — deleting ~5 of 6 step files and keeping the registry the
  single source of truth. Mechanical breakdown in **R-08**. Trade-off to handle while
  implementing: enhancers that today return a narrower type (`NetworkEnhancer -> nx.Graph`,
  `TaxaEnhancer -> list[Match]`) either wrap-and-return `Analysis` or stay as internal helpers
  behind a thin `Analysis`-keyed adapter — keep the pure compute independently testable.

### 9b. Still open — your call

- **E-04  Persist a versioned `RunResult`/`Analysis` on-disk format.** `batch_runner` already
  pickles `analysis.pkl`; a documented, versioned format (or a "load pickle → serialize ttl"
  path) would make RDF export reproducible without re-running the pipeline. Ties into F-05/F-06.

---

## 10. Suggested sequencing for the implementer

1. **Delete the dead cluster** (§2) — instantly shrinks the surface and removes broken imports.
   Port F-01/F-02 snippets out of the files being deleted *first*.
2. **Fix P0 bugs** (B-01, B-02, B-03-as-deletion) so the DB builds and MS2 runs.
3. **Settle the enhancer contract** (B-07 → uniform `Analysis` contract, E-03) and fix
   B-04/B-05/B-06 while in the enhancers.
4. **DRY passes** (R-01, R-02, R-03) — safe, mechanical, big line savings.
5. **Add the T1 unit tests** (they also pin the refactors above), then T2/T3 (T3 pins step
   behaviour before R-08), then the integration markers (T4) and the `--neg` smoke path (E-02).
6. **Collapse the step layer** (R-08 / E-03) now that T3 pins step behaviour.
7. **Introduce tooling** (E-01: ruff + pre-commit + CI) once §2/§3 have landed so the first
   `ruff` run is near-clean and CI can gate the fast suite.
8. **Docs** (§7): README rewrite + `docs/ARCHITECTURE.md`, then the smaller updates.
9. **Finish features** (§4) and wire RDF export (F-05) as capacity allows.
10. **Optimize** (§5) last, with measurements.
