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

### 2026-10-02 — Vocabulary made publishable: fixes, statuses, and every non-metadata term emitted

- **Datatype ranges.** Measured on the 2026-09-17 export: `enpkg:clusterConnectivity` (653
  literals) and `enpkg:ingredientCount` (69) were emitted as `xsd:integer` under a declared
  `xsd:double`, and `enpkg:multimerFactor` as both. OWL 2 treats the two datatypes as disjoint, so
  a reasoner reads each such literal as a contradiction. All three are counts, so their ranges are
  now `xsd:integer`; CGC is mzAdan's "number of ions contained in the cluster", which is what
  `len(members)` computes. The recipe fields stay `float` in Python and are cast on emission, so
  recipe hashes, and therefore recipe and adduct IRIs, do not change.
- **Other defects in emitted terms.** DOI nodes were typed `emi:BibliographicResource`, which EMI
  does not declare (it uses `dcterms:BibliographicResource`); now `dcterms:`. `enpkg.ttl` used
  `rdfs:definedBy`, which RDFS does not define (the term is `rdfs:isDefinedBy`), and imported
  `<https://w3id.org/emi#>` while EMI's ontology IRI is `<https://w3id.org/emi>`; the ontology's own
  IRI dropped its `#` to match. `enpkg:adductMass` was `skos:exactMatch MS:1003243`, but PSI-MS
  defines that as the ion's *mass* while ours is its *m/z*, so it is now a `skos:closeMatch`.
- **Statuses moved to `vs:term_status`.** The `[live]`/`[target]` tags lived inside
  `rdfs:comment`, mixing development state into the published definition and forcing the drift
  test to parse prose. Each term now carries `vs:term_status` (W3C SemWeb Vocab Status): `testing`
  for emitted terms until the first published release, `unstable` for declared-but-not-emitted.
  Every term also gained an `rdfs:label` (53 had none) and a definition (26 had only the tag), and
  comments narrating history or naming serializer functions were rewritten as definitions.
- **New drift tests**, each motivated by a defect above that no test caught: emitted `enpkg:`
  literals must carry their declared range; emitted `emi:` terms must exist in `EMI-vocab.owl`;
  `enpkg.ttl` must import EMI by EMI's ontology IRI and use only RDF/RDFS/OWL/SKOS terms those
  vocabularies define; every term needs a label and a definition. Each was checked to fail on the
  defect it targets.
- **Five declared terms wired in.** `enpkg:Ingredient` is stamped on ingredient nodes;
  `enpkg:adductFormula` puts the bracket form on recipe nodes; `enpkg:algorithm` records the matchms
  similarity (`spectral_match_params.method`) on each MS2 match; `enpkg:siriusVersion` comes from
  `sirius --version`, since the summary TSVs carry no version (13 checked). That call takes about
  7 s (JVM start-up), so it is cached per executable for the process; an unreadable version emits
  nothing, which is also what precomputed SIRIUS output will get. `enpkg:annotationMethod`, declared
  without a definition, now distinguishes the two ways the MS1 path produces a hypothesis —
  `precursor-mass-search` (anchors and singletons) and `cluster-anchor-inheritance` (satellites,
  which get their anchor's molecule re-cast under their own recipe without a mass search). The
  value is stamped where each path builds the `ChemicalAdduct` rather than inferred from the
  cluster role at serialization time, so it records what happened.
- **Molecular-formula subgraph.** `ChemicalStructure` and `AdductAnnotation` nodes link to one
  shared `enpkg:MolecularFormula` node per composition and charge (keyed on the Hill form), with
  one `enpkg:Atom` per element. Measured on one stored analysis: 3,126 formula nodes, 11,957 atoms,
  39,943 links, about 97k triples on 1.17M. All 4,139 formula strings of the 2026-09-17 export
  parse; 151 carry a charge suffix (permanently charged molecules such as quaternary ammoniums), so
  a new `enpkg:netCharge` (requested during review) holds the molecule's net charge, and a charged
  formula is a separate node from the neutral one. `atomCount` is `xsd:positiveInteger`.
  `enpkg:isotope` stays declared but `unstable` until a source provides isotope-labelled formulas.
  The `chemrof:generalized_empirical_formula` literals stay as they are; whether to keep both forms
  is deferred.
- **`.gitignore`'s `data` rule anchored to the root.** The bare `data` pattern was meant for the
  repository-root `data/` folder but matched every directory named `data`, including the source
  package `enpkg/monolith/data/`: each file there had to be force-added, and a new one
  (`molecular_formula.py`) was silently skipped by `git add`. It is now `/data/`, plus an explicit
  `enpkg/tests/data/`, which `enpkg/tests/test_enhancers/conftest.py` relies on being ignored (the
  locally built integration fixtures).
- **Stored artifacts need regenerating.** `ChemicalAdduct.annotation_method`,
  `MS2ChemicalAnnotation.algorithm` and `SiriusChemicalAnnotation.sirius_version` are new fields,
  so `analysis.pkl` files written before this change cannot be serialized with `enpkg serialize`.
  The August 2026 pickles in `gui_workspace/logs/` already could not: their recipes use the
  `ammonium` ingredient renamed to `ammonia` in `20aa585`.

### 2026-10-01 — Library-only MS2 matches need a stronger score

- **The problem.** A LOTUS-backed MS2 match is weighed against the sample's taxonomy through
  the organisms LOTUS reports for its structure. A library-only match names a structure LOTUS
  does not know, so nothing weighs it, and general-purpose libraries hold drugs and screening
  compounds as well as natural products. On the fixture, an *Arnica montana* extract, feature
  105 was annotated with simurosertib (a kinase inhibitor, cosine 0.69), levosulpiride (an
  antipsychotic, 0.61), tepraloxydim (a herbicide, 0.55) and two screening compounds.
- **Alternatives measured** on the fixture's 91 library-only structure–feature pairs:
  - *Class-level taxonomy*, the closest LOTUS organism producing any compound of the
    structure's chemical class: 67 of the 91 have neither an NPClassifier nor a ClassyFire
    class, and the 18 with an NPClassifier class all score at class level (Magnoliopsida) or
    closer. Oxybutynin, an antimuscarinic drug classified "Hydrocarbons", scores as if
    reported from *Arnica montana* itself. It does not discriminate.
  - *Source collection*: 19 of the 91 come from MSnLib's drug and screening collections, but
    GNPS redistributes those, so FragHub holds each such spectrum twice and a filter on the
    source file would have to catch both copies.
  - *Natural-product-likeness* (Ertl et al. 2008), computed from the structure, separates
    synthetic compounds from natural products, but needs RDKit, which is not a dependency.
- **The change.** A library-only match is annotated only when its cosine reaches the new
  `SpectralMatchParams.library_only_min_score` (default 0.7, inclusive), which the run setup
  shows under the spectral-matching settings. It is not a taxonomic check: it asks for
  stronger spectral evidence where no taxonomic evidence exists. It is applied after the
  InChIKey checks, so malformed keys are still reported whatever their score. 0.7 is the
  same provisional default as `ms2_coupling_min_score`, whose entry below cites the
  literature on cosine thresholds. Setting it to `min_score` annotates every library-only
  match that clears `min_score`, as before.
- **Effect on the fixture**, from one run at 0.2 and one at 0.7: library-only annotations go
  from 91 on 41 features to 34 on 21, their graph nodes from 67 to 27, and 10 features lose
  their only MS2 matches (56 instead of 66 have any). All of feature 105's drugs and screening
  compounds go. LOTUS-backed annotations and the emitted MS1 hypotheses do not change, the
  latter because library-only matches below 0.7 already did not couple with MS1. High-cosine
  matches to compounds that are not natural products remain, such as
  tris(2,4-di-tert-butylphenyl) phosphate at 0.849, a plastic additive that is a common LC-MS
  contaminant and probably a correct identification.

### 2026-10-01 — Adduct labels name ammonium correctly; charged ingredients carry ion masses

- **Labels.** The ingredient named `ammonium` weighed 17.02655 Da, the mass of ammonia (NH3);
  recipes add a proton to it, so the masses were right. The serializer printed it as `NH4`,
  so the graph published `[M+NH4+H]+` for [M+NH4]+, `[M+NH4+2H]2+` (three charges on a 2+
  ion) for [M+H+NH4]2+, `[2M+NH4+H]+` for [2M+NH4]+ and `[M-NH4+H]+` for the MS1 adduct
  graph's [M+H-NH3]+, and the recipe nodes listed one ammonium beside one proton. The
  ingredient is now `ammonia`, printed `NH3`, and each ammonia a proton accompanies is printed
  `NH4` together with that proton: `[M+NH4]+`, `[M+NH4+H]2+`, `[2M+NH4]+`, `[M-NH3+H]+`.
  Every other label is unchanged, and so are masses and plausibility scores. The five
  recipes' URIs change, since recipe URIs are hashed from ingredient names; graphs serialized
  earlier need regenerating to agree.
- **Masses.** Seven ingredients carried the neutral atom's mass while every recipe uses them
  as ions: each recipe's charge is the sum of its ingredients' ionic charges, as in
  [M-H+Mg]+ (−1 + 2), which a test now checks for every recipe. Sodium and potassium were
  0.55 mDa too heavy, magnesium, calcium and iron 1.10 mDa too heavy, chlorine and bromine
  0.55 mDa too light; [M+Na]+ at m/z 200 was computed 2.7 ppm too heavy, against the
  ±10 ppm window. The table now holds the ions' masses (Na+, K+, Mg2+, Ca2+, Fe2+, Cl−,
  Br−). The proton and the neutral molecules were already right to 0.005 mDa. The tests
  compute the expected m/z from atomic masses and the electron mass, not from the table.
- **Effect on the fixture**, measured by running the MS1 adduct graph and MS1 with each
  table: the MS1 hypotheses change on 374 of 660 features, 563 lost and 518 gained out of
  10,252, mostly metal and multiply charged forms; two features change from anchor to
  singleton in the adduct graph.

### 2026-10-01 — One MS2 annotation per structure and feature

- **The problem.** The MS2 enhancer made one annotation per matching library spectrum, but
  the graph keys an MS2 annotation node on feature, library and 2D InChIKey. A library often
  holds several spectra of one compound (collision energies, instruments), and FragHub's
  export holds the MSnLib collections twice, since GNPS redistributes them. On the fixture,
  1,569 LOTUS-backed matches named 113 distinct structure–feature pairs, one structure 331
  times on one feature, and 249 library-only matches named 91. Three consequences: copies of
  one structure took several of a feature's `top_k_ms2` slots; the node they shared received
  one `enpkg:annotationRank` per copy (57 nodes had several), while only the first copy's
  score was written; and the MS2 reweighting, which sums over a feature's matches, weighted a
  structure by its number of library spectra.
- **Correction.** The 2026-09-30 entry's volume figures (1,569 → 1,818 annotations, 1.16×)
  counted matches, not structures. Counted per structure and feature, library-only matches
  add 91 to 113.
- **The change.** Once a chunk is scored, each feature keeps one annotation per library and
  2D InChIKey: highest cosine, then most matched peaks, then the lowest library spectrum id.
  The reduction runs after `_annotate`, so every scored match is still classified and the
  malformed-InChIKey report still names every malformed value; collapsing before `_annotate`
  would have merged distinct malformed strings that share their first 14 characters. The run
  log reports how many matches were merged.
- **Candidate order.** `get_candidate_spectra` orders its pairs by query, then library
  spectrum id, so the last tie-break does not rest on how DuckDB executes the join. The order
  was not observed to vary. The test that pins it also passes without the `ORDER BY` at test
  scale, so it records the contract rather than catching its removal.
- **Effect on the fixture**, measured from one run with and one without the reduction: the
  graph's MS2 nodes go from 96 to 167 on the same 66 features, and none carries several
  ranks; 18 features now show 4 or 5 distinct structures, where none showed more than 3. MS2
  propagated scores change on 362–376 features, by up to 0.25; MS1 scores do not change. The
  emitted MS2 matches change on 31 of the 66 features, the top-ranked one on 1; the emitted
  MS1 hypotheses change on 4 features, through the MS1–MS2 coupling.

### 2026-10-01 — Reweighting gives the same scores on every run

- **The problem.** Two runs of unchanged code gave propagated NPC scores differing by up to
  0.195 on about half the features, and graphs differing by 643 triples (784,039 against
  783,396). The 2026-09-30 entry placed the cause upstream of MS2. It is the label propagation
  in `WeightsEnhancer`, which runs after MS2 and computes both the MS1 and the MS2 scores.
- **How it was located.** Two runs in separate processes, with different hash seeds, gave
  identical outputs at every stage before propagation: the molecular network, the adduct
  graph, the LOTUS row order, every MS1 and MS2 annotation, and the NPC matrices handed to
  propagation. Propagation repeated on identical input in one process differed by up to 0.195
  with Numba's 16 threads, and not at all with one.
- **The cause.** `numba_label_propagation` updates nodes in a `prange` loop, which Numba
  splits across threads. Inside the loop it marked each node as having values in the mask
  that other nodes read during the same iteration. A neighbour visited later then added that
  node's edge weight to its denominator while the node's values, read from the start of the
  iteration, were still zero, which lowered its average. Which neighbours were already marked
  depended on thread timing, and with one thread on node order: on a four-node chain whose
  one end carries a vector, the chain ended at 0.5 or 0.7 depending on the order. What
  `WEIGHTS_ENHANCER.md` describes, un-annotated features "filled purely from their
  neighbours", gives 1.0.
- **The fix.** Each iteration reads the mask as it stood at its start and marks nodes in a
  copy, which the next iteration reads. Results are now bit-identical for any thread count,
  and equal to within rounding (1e-16) for any node order. Verified end to end: two runs in
  separate processes give bit-identical scores and the same graph (783,949 triples).
- **Effect on the fixture**, measured against one run of the previous code on the same
  inputs: propagated scores change on 357–370 of the 660 features, by up to 0.47. The MS2
  scores were the most diluted, since few features carry LOTUS-backed MS2 matches and most
  started without values: their mean sum per feature rises from 0.22 to 0.93 at class level.
  In the graph, the emitted MS1 hypotheses change on 9 of 606 features (the top-ranked one
  on 5); the emitted MS2 matches do not change.
- **LOTUS row order.** The canonical LOTUS list was sorted on the 2D InChIKey alone, so
  stereoisomers sharing it came back in whatever order DuckDB's sort produced. The first row
  of each group supplies an MS2 match's NPC vectors and structure node, and for 7,194
  structures the stereoisomers' NPC vectors differ. Ties are now broken by full InChIKey,
  then SMILES, the table's key. The order was not observed to vary between runs, but nothing
  guaranteed it. On the fixture the new order moves propagated scores by at most 0.001.
- **Not changed.** Propagation still converges to practically one profile per connected
  component of the network, because no feature is held at its own vector: on the fixture,
  profiles within a component differ by at most 0.002, and the largest component holds 300
  of the 660 features. `WEIGHTS_ENHANCER.md` said annotated features "anchor their
  neighbourhood"; it now describes the convergence. Whether propagation should keep part of
  each feature's own vector is open.

### 2026-10-01 — MS2 matches couple with MS1 only above a score threshold; library-only matches couple on formula

Revises the coupling consequence recorded in the 2026-09-30 entry below.

- **The problem.** The serializer kept, on any feature with an emitted MS2 match, only the
  MS1 adduct hypotheses sharing the match's 2D InChIKey. Once library-only matches reached
  the graph, every feature whose matches were all library-only lost all its MS1 hypotheses,
  since no LOTUS candidate can share a library-only structure's InChIKey: 20 features and
  100 adducts on the fixture, measured before this branch moved onto `GUI_MIGRATION`. The
  removing match could be as weak as cosine 0.215. The same rule already let weak
  LOTUS-backed matches remove MS1 hypotheses (10 features).
- **Threshold.** New `SerializerConfig.ms2_coupling_min_score` (default 0.7, range 0–1),
  which reaches the GUI's run setup, the run YAML and the CLI with no other wiring. Only an
  MS2 match at or above it couples; a weaker match is still emitted, but neither removes nor
  keeps MS1 adducts and gets no link. 0 makes every emitted match decide, the previous
  behaviour. The 0.7 default is the GNPS library-search convention and is provisional. In
  Scheubert et al. 2017 most of 70 public datasets reached 1% FDR at cosine 0.6–0.65, with
  the needed cosine depending on how many peaks must match. Li et al. 2021 recommend
  scores above 0.75 for dot product. The 0.2 `min_score` this pipeline matches with comes
  from Rutz et al. 2019, where it gated predicted ISDB spectra before taxonomic re-ranking:
  an entry gate, not a confidence level.
- **Formula coupling.** A library-only match above the threshold keeps the MS1 hypotheses
  whose molecular formula equals the library structure's. LOTUS holds at most isomers of
  that structure, so the formula is what an ionisation hypothesis can share with it.
  Formulas are compared as strings: on both sides they are in Hill order with the same `+`
  charge suffix (measured: no library-only formula out of Hill order; 8 inorganic LOTUS
  formulas such as `HCl`, which cannot meet a library match). LOTUS-backed matches keep the
  2D InChIKey rule. It selects the same hypotheses, because each MS1 hypothesis groups
  every LOTUS isomer of one formula.
- **Vocabulary.** Both kinds of correspondence use `enpkg:hasCorrespondingAdduct`, whose
  `rdfs:comment` changed from "the MS1 adduct hypothesis proposing the *same* compound
  (shared 2D InChIKey)" to the meaning it carried in its second half: the hypothesis that
  explains how the MS2-identified molecule ionised, i.e. one with the molecule's formula.
  The comment states that the matched structure is among the candidates only when LOTUS
  knows it, and that the link is asserted only above the coupling threshold, so its
  absence does not mean that no hypothesis corresponds. Every user's graph inherits this
  wording through the inlined `enpkg.ttl`.
- **Measured effect** on the fixture (660 features, *Arnica montana*,
  `FRAGHUB_POS_LC:2026.03`), from one pipeline run serialized under each rule. Without any
  coupling, 2,633 MS1 hypotheses are emitted.

  | Rule | MS1 hypotheses removed | Features left with none |
  |---|---|---|
  | LOTUS-backed matches only, any score (before 2026-09-30) | 196 | 16 |
  | Library-only matches added, formula coupling, any score | 282 | 27 |
  | Same, threshold 0.5 / 0.6 / **0.7** / 0.8 | 210 / 190 / **172** / 147 | 18 / 14 / **12** / 9 |

  At the 0.7 default the graph loses fewer MS1 hypotheses than before library-only matches
  were emitted. Of the MS2 matches in the graph, 29 of 56 LOTUS-backed and 12 of 41
  library-only reach 0.7.

### 2026-10-01 — FragHub's NPClassifier labels are restored on import

- **The problem.** FragHub copies its NPClassifier labels from an ontology table it ships
  (`datas/ontologies_datas/ontologies_dict_part_*.csv`). In that table the substrings `nan`
  and `none` are deleted inside labels (`Lignans` → `Ligs`, `Flavanones` → `Flavas`), and
  labels containing `", "` are split into separate `|`-separated labels
  (`Carotenoids (C40, β-β)` → `Carotenoids (C40|β-β)`). 88,825 of the table's 1,001,751
  InChIKeys (8.9%) are affected; in our `FRAGHUB_POS_LC:2026.03` export, 103,210 label
  occurrences.
- **Where it comes from**, traced one step at a time: not our importer, which compares
  placeholders as whole values; present verbatim in the export file; copied unchanged by
  FragHub's `ontologies_completion()`; present in both versions of the table in FragHub's
  history (`7cb091f`, 2024-12-09, and `1ea0796`, 2025-09-01). The program that generated
  the table is not in FragHub's repository, so the cause is inferred, not verified: both
  rules match a list-to-text conversion that removes `nan`/`None` by substring replacement
  and joins then splits on `", "`.
- **The repair** (`loaders/spectral_libraries/npc_labels.py`) rewrites every term of the
  vendored NPClassifier vocabulary by the same two rules and replaces any run of
  neighbouring labels that equals a rewritten form with the term. It never replaces a run
  containing a genuine term, leaves a fragment whose partner is missing, and skips a form
  two terms share. It runs after the `INSERT` on distinct cell values (one `GROUP BY` per
  column and an `UPDATE` joined on a small table), so memory stays flat, and it logs every
  repair with the number of spectra it touched. `preview()` shows restored labels.
- **Vocabulary.** The repair uses the vendored EMI vocabulary rather than LOTUS's NPC column
  names. EMI is always in the repository, whereas `_meta_columns` exists only if LOTUS was
  imported into the same database. EMI recognises all 61 corrupted labels in our export,
  plus `Purine nucleos(t)ides`, which LOTUS's columns lack. It lacks two genuine
  superclasses, `Alkylresorcinols` and `Sphingolipids`, which therefore pass through
  unrecognised. NPClassifier's own `index_v1.json` has all of them; the repair proposed to
  FragHub uses it. `NpcVocabulary.labels(rank)` is new, for this.
- **Measured on our library** (read-only simulation over its distinct cells): all 16,394
  superclass and 75,483 class label occurrences are restored (74,834 spectra), and no class
  label is left outside the vocabulary. A database keeps the corrupted labels until its
  library is re-imported.
- Nothing reads these labels yet: library-only annotations carry them but neither the
  reweighting nor the serializer uses them. The repair makes them correct before a consumer
  exists, for every user who imports a FragHub export.
- The issue for FragHub, and a repair script tested on a copy of their table, are in
  `docs/upstream/FRAGHUB_NPCLASSIFIER_LABELS.md`.

### 2026-09-30 — MS2 annotates library matches that LOTUS does not know

- **The problem.** `Ms2Enhancer._annotate` discarded any spectral-library match whose short
  InChIKey was absent from LOTUS, silently: no log line, no counter. In
  `FRAGHUB_POS_LC:2026.03` that is 948,975 of 1,450,368 spectra (65.4%), covering 194,515
  distinct structures against the 9,777 LOTUS knows. The structure metadata needed to
  annotate them was already on every `LibraryCandidate` and read by nothing.
- **What changed.** A LOTUS miss now yields a *library-only* annotation: `source` is the
  library's `name:version`, `library_structure` carries the library's InChIKey, InChI, SMILES,
  formula, name and NPClassifier/ClassyFire labels, the classification vectors are zero-filled
  at full vocabulary length, and `organisms` is empty. The serializer emits it an
  `emi:ChemicalStructure` node, reusing only terms `_add_compound` already emits. Three things
  `_add_compound` emits have no counterpart: `emi:inTaxon` and `prov:wasDerivedFrom` (the library
  names no organism and no reference), and ClassyFire *subclass*, which the library carries but
  which has no `enpkg:` term; it is kept on `LibraryStructure` and not emitted, since adding a
  term is vocabulary work to settle on its own.
- **Decision: graph only, no reweighting.** The library gives one NPC *label* per rank where
  LOTUS gives a probability per term. Using a label in the reweighting would mean one-hot
  encoding it, which drops the confidence gradation (Quercetin's 0.9955 Shikimates becomes a
  flat 1.0) and flattens genuine ambiguity. It would also need a taxonomical similarity for an
  annotation with no organism, and because similarities are normalised per feature, any value
  there shifts weight away from LOTUS-backed matches. And it would help few: only 7,453 of the
  194,515 recovered structures carry an NPC pathway at all. So library-only annotations have no
  organism and `WeightsEnhancer`'s existing `has_organisms()` filter keeps them out.
  `test_weights_ms2_classifications.py` pins that the MS2 feature matrices are bit-identical
  with and without them.
- **Decision: LOTUS keeps precedence on classification.** When LOTUS knows the structure, its
  vectors and organisms are used exactly as before and no library label is consulted.
- **LOTUS-backed MS2 matches now emit their compound node too.** `_add_compound` was called
  only from the MS1 path, so a LOTUS-backed MS2 match on a feature with no corresponding MS1
  adduct resolved to a bare `emi:InChIKey2D` node. Left alone, library-only matches would have
  carried more structure detail than LOTUS-backed ones. `MS2ChemicalAnnotation.lotus` now holds
  a reference to the matched entry (already retained by `LotusStore`, so one pointer), and
  `_add_ms2_annotation` emits it.
- **Cosine breaks ties in the MS2 ranking.** Library-only annotations all have an alignment
  score of 0.0, and `sorted` is stable, so `top_k_ms2` kept whichever were stored first. The
  sort key is now `(alignment, cosine)`. MS1 passes no fallback key, so its order is unchanged.
- **Malformed InChIKeys are refused and reported.** 287 rows of the library (3 distinct values)
  carry a SMILES in the InChIKey field, e.g. `CCCCCCCCCCCCCC`. It is 14 uppercase letters, so
  only the full key's shape (`[A-Z]{14}-[A-Z]{10}-[A-Z]`) tells it apart. Before this change
  those rows were dropped with the other LOTUS misses; annotating them would have minted
  `identifiers.org/inchikey/CCCCCCCCCCCCCC`. They now produce no annotation, and `enhance()`
  warns with the count and the distinct offending values.
- **Measured effect** (fixture dataset, 660 features, against the workspace database): MS2
  annotations in memory 1,569 → 1,818 (1.16×; the 1,569 LOTUS-backed ones unchanged, 249
  library-only added). The pre-cosine candidate split was 49/51, so library-only candidates
  clear `min_score`/`min_peaks` far less often. Zero vectors cost 3,120 bytes per library-only
  annotation. MS2 `enhance()` 9.0 s → 9.28 s.
- **Consequence for MS1 in the graph.** `_emit_ms1_annotations` keeps, on a feature with any
  emitted MS2 match, only the MS1 adducts sharing a 2D InChIKey with one. MS1 candidates come
  only from LOTUS, so a feature whose MS2 matches are all library-only keeps no MS1 adduct.
  Measured: 20 features newly gained MS2 matches and all 20 lost their 5 adducts, 100 of 2,795
  (3.6%), for a net −1,225 structure nodes and −30,879 triples. That coupling rule is documented
  as intended (MS2_ENHANCER.md §6); this change only makes it fire more often.
- **Pipeline output is not run-to-run deterministic, independently of this change.** Two runs
  of the unchanged code gave propagated MS2 scores differing by up to 0.041 and graphs of
  585,466 against 585,568 triples. The source is upstream of MS2 and was not investigated.
- The design notes previously held in a TODO inside `_annotate` are resolved by the above and
  removed.

### 2026-09-18 — `unwrap_optional` missed `T | None` before Python 3.14

`unwrap_optional` tested `get_origin(annotation) is Union`. That recognises `Optional[T]` on
every version, but `T | None` only on Python 3.14, where `typing.Union` and
`types.UnionType` became the same object. On 3.13 and earlier the two spellings have
different origins — `typing.Union` and `types.UnionType` — so `T | None` fell through to the
"not optional" branch. It now matches either origin.

The consequence was not confined to the failing assertion. `unwrap_optional` is what both
form builders call to decide whether a field may be left empty, and `enum_choices` calls it
before looking for a `Literal`, so on 3.13 a field annotated `Literal[...] | None` would
have rendered as free text instead of a dropdown, and no optional field written in the
`|` spelling would have been treated as optional.

**Local runs could not have caught this.** The development interpreter is 3.14, where the
unification makes the original check correct; the bug is only reachable on an older one.
It was found by the 3.13 CI worker. Verified under pre-unification semantics on a 3.11
interpreter before committing, since no 3.13 is installed here: `get_origin(int | None)`
returns `types.UnionType` there, and the fixed predicate resolves all of `Optional[T]`,
`T | None`, a plain type and a non-optional `int | str` correctly.

### 2026-09-18 — The MS1 precursor window is in ppm

Carried out the decision left open as B-11. `ms1_enhancer` matched on
`spectral_match_params.parent_mz_tol`, an absolute Dalton tolerance, where the workflow this
pipeline replaced used a relative one. A fixed 0.01 Da window is 50 ppm at m/z 200 and
10 ppm at m/z 1000, so it was five times more permissive at the bottom of the natural-product
mass range than at the top — the opposite of how instrument mass accuracy behaves.

`ms1_ppm_tol` (default 10 ppm) is a new field on `SpectralMatchParams`, applied at both
places MS1 uses a window: the LOTUS exact-mass query bounds and the per-feature adduct
bisect. **`parent_mz_tol` is unchanged and now governs MS2 alone.** It stays in Daltons
because the MS2 candidate query is what makes the SQL range join provably the same predicate
as matchms' `PrecursorMzMatch(tolerance, "Dalton")`; expressing it in ppm would break that
equivalence and the test that pins it.

**This changes MS1 results.** At the default the window is tighter than before everywhere
below m/z 1000, so features will carry fewer candidate adducts — which is the intent, but
existing MS1 annotations are not comparable across the change.

`MS1GraphEnhancerConfig.mz_tolerance` remains in Daltons and its docstring no longer claims
to track `parent_mz_tol`. That tolerance is applied between two *observed* features, where a
relative window is a different question from comparing an observed m/z against a theoretical
adduct mass. Whether it should also become relative is untouched here.

### 2026-09-17 — `docs/REFACTORING_PLAN.md` removed

The plan described an architecture that no longer exists: `DBLoader`, the `spectral_library`
table, `scripts/build_duckdb.py`, `gui/app.py` and the Streamlit interface. Its P0 finding
B-02 was "the DuckDB build script is broken", about a script that has since been split and
renamed. A document that reads as current while describing a superseded system is worse than
no document, and its remaining accurate parts are now covered by
[BUILDING_THE_DATABASE.md](docs/BUILDING_THE_DATABASE.md), [ADDING_A_BLOCK.md](docs/ADDING_A_BLOCK.md)
and [webui/ARCHITECTURE.md](enpkg/monolith/webui/ARCHITECTURE.md). It remains in git history.

Findings were checked before deleting rather than assumed stale. B-09 (a duplicated
`ott_matches` field) is fixed; B-10's `TaxonomicalEnhancementStep` no longer exists. B-08
(inert `ReweightingParams` knobs) and F-03 (SIRIUS results are parsed but never attached to
the `Analysis`) are still true and were already described in full in the docstrings that
cited them — only the dangling file reference was removed from each.

**One finding had no other home and is carried here. B-11: the MS1 precursor window is in
Daltons, not ppm.** `ms1_enhancer` matches on `spectral_match_params.parent_mz_tol`, an
absolute tolerance, while the historical workflow this pipeline replaced used a relative ppm
tolerance for MS1 annotation. The two select different adducts, and the divergence is
widest at the extremes of the mass range. Whether Daltons is intended has never been
settled; if ppm is wanted it needs a new field on `SpectralMatchParams` rather than a
reinterpretation of the existing one.

### 2026-09-17 — InChI is a column, not metadata

FragHub's POS_LC export carries an `INCHI` column the ingest schema did not recognise. It
was preserved in `metadata_json` and recorded in `spectral_library_registry.unknown_columns`
— the "core required, extras allowed" contract behaving as intended. Nothing was lost, but
nothing on the annotation path could read it either.

**It is promoted to a real column so the annotation path can reach a second, canonical
structure representation without parsing JSON.** InChI is what an RDF export should carry
for a structure: it is standardised and comparable across sources in a way SMILES is not.

The original argument for promoting it was that InChI would be the only structure available
for the spectra lacking SMILES. **That was inferred from FragHub's documented behaviour and
is false for this export.** Measured on the ingested bucket: `inchi` and `smiles` are
non-null on exactly the same 1,445,449 of 1,450,368 rows, and the count of rows with InChI
but no SMILES is zero. The promotion still stands on the argument above, but it is a
convenience, not a recovery of otherwise unreachable data.

**The 4,919 spectra without SMILES have no InChI either — they carry an InChIKey and no
structure at all.** FragHub's paper states that spectra lacking both InChI and SMILES are
dropped; these rows contradict that, or the guarantee is narrower than it reads. It matters
for the planned library-identity fields on `MS2ChemicalAnnotation`: those spectra can be
matched and named, but there is no structure to serialize for them, so any identity field
beyond the InChIKey has to be optional.

**A re-import alone would not have applied the change.** `create_schema()` is written with
`CREATE TABLE IF NOT EXISTS`, so running it repeatedly is harmless — and by the same token
it does nothing at all to a table that already exists, including adding a column to it.
Re-running the importer would have deleted and reinserted rows into the old 22-column
table. `library_spectra` was dropped so the schema could recreate it, then re-ingested from
the same export; the LOTUS tables were untouched.

The column is now required by the read path: `get_candidate_spectra` selects every column
and `LibraryCandidate.from_row` reads `inchi` by name, so a database written before this
change fails with `KeyError: 'inchi'` instead of quietly annotating without it.

**Rebuilding the fixtures exposed a bug in `build_test_fixtures.py` that had never run.**
It reads the column list from `information_schema.columns` to name the columns explicitly
instead of copying positionally. The source database is attached as `src`, and
`information_schema` spans every attached database, so each name came back twice and the
insert failed with `Duplicate column name "id"`. The lookup is now restricted with
`table_catalog = current_database()`. The named-column copy dates from the
`spectral_library` → `library_spectra` rename and had not been executed since, because the
fixture database still carried the pre-rename schema.

### 2026-09-17 — SIRIUS summaries were written to an unwritable path

A run with the SIRIUS block selected computed for 25 minutes and then reported
`No SIRIUS summaries directory`, leaving the analysis unchanged and the graph without a
SIRIUS layer. The run itself was recorded as successful.

**The summaries path was relative.** The project path passed to `-o` was already resolved
to an absolute path, but the `--output` given to `write-summaries` was the raw
`output_directory` string from the configuration. `sirius.exe` resolves a relative path
against its own installation directory rather than the working directory it was launched
from, so on Windows it tried to create `C:\Program Files\sirius\sirius_output` and was
refused. Both paths are now built from the same resolved project directory.

**The summaries directory is now per-run.** It sits inside the stamped project directory
rather than beside it. The previous flat layout gave every sample of a batch the same
summaries directory, so each export overwrote the one before it.

**Nothing reported the failure.** SIRIUS logged the error and still exited zero, so
`check=True` did not fire and the branch that logs its output never ran. The only signal
reaching the pipeline was the absent directory, which was treated as a warning. That
warning is unchanged for now: a block that raises aborts the run and suppresses the Turtle
export, so failing here would discard the work of every other block. Whether an absent
summaries directory should stop a run is still open.

### 2026-09-14 — The NiceGUI front end

Three pages — input data, pipeline configuration and run, serializer options — under
`enpkg/monolith/webui/`, launched by `enpkg gui` or `enpkg gui --native`. The Streamlit
interface stays until this one has been used on a real dataset. Design is written up in
[webui/ARCHITECTURE.md](enpkg/monolith/webui/ARCHITECTURE.md); this entry records the
decisions and what they cost.

**State is split by what it is made of.** Moving between pages is a full page load, so
nothing a user chose can live in a page function's variables; and a module-level dict, the
obvious alternative, is shared by every client in NiceGUI. So JSON-serialisable values go
in `app.storage.user` (per visitor, and surviving a browser reload, which
`st.session_state` did not) and everything else — subprocess handles, output buffers,
parsed results — goes in a module-level dict indexed only by the caller's own session id.
With one visitor, which is what the native window is, that degenerates to a single entry
with no special case.

**Runs execute as a separate process running `enpkg run`.** That is what gives output
while the run is going, a working Cancel, and survival across a browser reload — and it
means the command line and the interface exercise one execution path rather than two that
drift. The decision the rest depends on is that the code reading the subprocess's output
touches no element: it fills a buffer and nothing else, so when the client that started
the run is destroyed — which is what navigating to another page does — nothing is left
pointing at a dead element. Pages poll that buffer with a timer that dies with them.

The cost, already recorded when the artifact was added: the `Analysis` cannot come back
across a process boundary, so the interface can only show what the result file carries.

**`config_io.save_unified_yaml` gained a `serializer` argument** so the page can write that
section; `state`, `runs` and `forms` hold the logic and contain no page code, which is why
they are testable without a browser.

**Three defects the tests found, all in code written the same day.** Save read values that
a one-second timer writes, so saving straight after a change wrote the previous values —
Save and Run now read the widgets first. `state.raw` filled in missing values by replacing
the whole mapping, so one access finding it unfamiliar discarded every choice — it now
fills keys individually. And an assertion was passing for the wrong reason:
`should_see("Valid")` matched the heading "Validated configuration", so it had never
checked the status it appeared to; that heading is now "Configuration preview", which is
what made the Save defect visible.

**Two traps in the test harness**, both recorded because the symptom points nowhere near
the cause. The test-only entry script must not be named `*_test.py` or `test_*.py`: pytest
collects those, imports it, runs its `ui.run()` for real, and hangs collection of the whole
directory — while each file still passes on its own. And `Storage.clear` fails on Windows
whenever an atomic storage write is in flight, which leaves NiceGUI half-reset so every
later test 404s; that is an upstream bug, written up in
[docs/NICEGUI_STORAGE_CLEANUP_BUG.md](docs/NICEGUI_STORAGE_CLEANUP_BUG.md) and worked
around in a fixture. Not yet reported.

**pytest moved to 8.x** because pytest-asyncio requires it, and without pytest-asyncio
NiceGUI's async `user` fixture is collected, skipped and reported as a passing run.

**Known gap.** No test starts a real run from the page. The run registry is covered against
a stand-in subprocess and the page is covered up to the point of launching, but the join
between them has only been exercised by hand. That check belongs in the parity pass before
Streamlit is removed.

### 2026-09-10 — The `enpkg` command line

The pipeline could not be run without a browser. `pipeline/` imports no GUI code and was
kept that way deliberately, but the only entry point was `streamlit run`, so processing
a few hundred experiments on a cluster had no supported path. This adds one.

**`enpkg` is now an installable package.** `enpkg/__init__.py` did not exist, making
`enpkg` a namespace package that resolved only because the repository root happened to be
on `sys.path` — via `pythonpath = .` in `pytest.ini` and `PYTHONPATH=/app` in the planned
container. Meanwhile `packages = [{include = "monolith", from = "enpkg"}]` installed the
inner directory as a top-level `monolith`, which no import in the codebase agrees with. A
`[project.scripts]` console script needs a genuinely importable module, so the package is
now `{include = "enpkg"}` with tests excluded, and `poetry install` no longer needs
`--no-root`. `PYTHONPATH` becomes belt-and-braces rather than load-bearing; it is left in
place for the Streamlit entry point, which puts the *script's* directory on `sys.path`
rather than the working directory.

**Commands.** `run`, `batch` (+ `batch discover`), `serialize`, `config init|validate|show`,
`db lotus|spectral-library`, `blocks list`. Each is a thin adapter over the same runner
the GUI calls; no orchestration logic lives in `enpkg/cli/`. `enpkg gui` is not here yet —
it arrives with the NiceGUI application.

**Built on Typer**, which derives parsing, `--help` and nested subcommands from type
annotations. It adds six packages (rich, pygments, markdown-it-py, mdurl, shellingham,
annotated-doc); click and colorama were already present. `argparse` would have avoided
those at the cost of hand-rolled subparser nesting for seven command groups.

**`db` forwards rather than redeclares.** `enpkg db lotus` hands its arguments straight to
`enpkg/scripts/import_lotus.py`, so the flags are defined once. Both import scripts gained
`argv` and `prog` parameters to make that possible — `prog` so argparse's usage line reads
`enpkg db lotus` rather than `enpkg`, which is not a command that accepts those flags.

**`config init` writes a template, not a validated config.** Blocks whose config has a
required field with no default — only `MSEnhancerConfig.duckdb_path` today — get `null`
and are listed on stderr. Generating only the fields that happen to have defaults would
hide from the user that a value is needed at all.

**A JSON result artifact** (`pipeline/run_artifact.py`, `--json-out`). This is what lets a
caller that runs the pipeline as a separate process learn what happened: the `Analysis`
lives in that process's memory and cannot be returned, so the outcome, the per-stage
durations, the `AnalysisSummary` counts and the output paths are projected into a small
file instead. `schema_version` lets a reader reject a file it cannot interpret. The
alternative considered was scraping stdout, which would break whenever a log line is
reworded.

**One bug found by running it rather than by testing it.** Input discovery matched "any
accepted suffix", but metadata accepts `.tsv/.txt/.csv` while quant tables are `.csv`, so
a normal folder resolved metadata to the quant table and the run died deep in the loader
looking for `sample_filename_pos` among quant columns. `find_shared_metadata` had always
iterated suffixes in preference order for exactly this reason; the CLI now does too.

Verified end to end: 2159 spectra through the networking block, a 2.1 MB Turtle graph,
logs and artifact under `--output-dir`, and nothing written to `gui_workspace/`.

### 2026-09-10 — Groundwork for an `enpkg` CLI and a NiceGUI front end

Work on the `GUI_MIGRATION` branch. This entry covers the shared groundwork only; the CLI
and the NiceGUI application follow.

**Why this is happening.** Two problems, both consequences of the pipeline being headless
while the only way to reach it is a browser.

The pipeline under `pipeline/` imports no GUI code, but `pyproject.toml` declares no
`[project.scripts]` and the only `argparse` entry points are the standalone
`enpkg/scripts/*.py` utilities. There is therefore no supported way to run a batch on a
cluster. And the Streamlit front end pays a recurring cost to Streamlit's execution model
rather than to the problem domain: the `form_rev` counter exists only because Streamlit
ignores a changed `value=` for an already-registered widget key, a trailing `st.rerun()`
works around the execution panel rendering beneath a collapsed `st.status`, `_drain_queue`
runs only *after* `run_pipeline` returns so a run shows a spinner and nothing else until it
finishes, and there is no way to cancel a run. NiceGUI's elements are persistent Python
objects, which removes the first two by construction; running the pipeline as a subprocess
of the new CLI removes the last two, and means both front ends exercise one execution path.

**`configuration/introspect.py`** — the five Pydantic-reflection helpers that were private to
`gui/form_builder.py` now live next to the models they reflect over, so the Streamlit and
NiceGUI form builders cannot drift while both exist. Adding `enum_choices` there fixed a
live defect: `_render_field` dispatched on `annotation is bool/int/float/str`, which
`Literal[...]` does not match, so `SpectralMatchParams.method` and
`MSEnhancerConfig.ms2_adduct_filter` fell through to a free-text box and had to be typed by
hand. Both are dropdowns now. (`canopus_source` looked like the same bug but is a `str` with
a regex constraint, and already worked.)

**`config_io.inject_shared_params`** — moved out of `gui/app.py`. `GeneralParams` describes
the run, not the block, so it is collected once and merged into every section that declares
it; `build_configs` never did this, so any caller that skipped the GUI's private copy would
have built each block from its Pydantic defaults and silently processed a negative-mode
dataset as positive. It is now on the path every front end shares.

**`output_dir` on `run_pipeline` and `run_batch`** — `LOG_DIR` is a module-level *relative*
path resolved against the process working directory. That is survivable for a GUI launched
from the repository root and not for a command line invokable from anywhere, which would
scatter its logs. The default is unchanged, so existing callers behave as before.

**Run stamps are now unique.** The stamp was one-second granular, so two runs started in the
same second wrote to the same log paths and the second overwrote the first. Four random
characters are appended; the stamp still sorts chronologically.

**`BatchResult.runtime_log` removed.** The path was computed and stored but no handler was
ever attached, so the file was never created. `gui/app.py` gated the "Batch folder" caption
on that always-empty field, so the caption never appeared; it now checks `batch_dir`.

**`run_batch` no longer mutates its caller's list.** It removes `"sirius"` from the selection
when the executable cannot be validated, which silently changed the caller's list.

**`SerializerConfig`.** `AnalysisSerializer` took nine keyword arguments; the runners
hardcoded eight and derived the ninth, so eight were unreachable from any config file and
the GUI could reach none. They now come from a Pydantic model written under a `serializer:`
key, consumed by `runner.py`, `batch_runner.py` and `smoke_serialize.py` alike. A test
asserts the model's field names and defaults match the constructor's, so the two cannot
drift apart unnoticed.

**`include_network` removed entirely** — from the config, from `AnalysisSerializer`, and
from `smoke_serialize`'s flags. Whether the `emi:LFpair` edges belong in the graph is
already answered by whether the networking block is part of the run; a second switch could
only contradict that, and the runners were in fact overriding the serializer's own default
on every network run. The edges are now emitted whenever a network is present.

The trade-off accepted here: the edge set is worst-case quadratic in feature count, and
`weights` declares `depends_on=("network",)`, so selecting reranking pulls the networking
block — and therefore the edges — in with it. There is no longer a way to compute a network
for reranking while keeping its edges out of the graph. If a large experiment produces an
unusably large export, this is the cause, and the fix would be to reintroduce a size-based
gate rather than a semantic one.

**`max_ions_per_spectrum` now rejects a non-positive value.** The three `top_k_*` options
normalise `<= 0` to `None` meaning "emit all", but this one is stored raw and used as a
slice bound, so `0` drops *every* ion while writing `enpkg:maxIonsPerSpectrum 0` as
provenance — an empty result that reads as deliberate. It was latent because `include_ions`
defaults off, and a serializer UI is exactly what turns it on. Pydantic now rejects it at
the config boundary (`gt=0`). This puts a hard requirement on the NiceGUI form builder: it
must return `None` for an empty optional number, since substituting `0` the way the
Streamlit builder does would now fail validation.

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
