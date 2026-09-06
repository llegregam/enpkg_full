# Schema Review & Vocabulary Plan

> **Status:** review of [`_static/MAIN_SCHEMA.mmd`](_static/MAIN_SCHEMA.mmd) against the previous
> diagram ([RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md)), the mapping worksheet
> ([RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md)) and the live
> [`AnalysisSerializer`](../enpkg/monolith/rdf/serializer.py). Every external accession and EMI term
> quoted below was checked against the vendored ontologies in [vocab/](vocab/).
>
> **Updated 2026-08-07** — the diagram changed twice since the initial pass (2026-08-05): the
> MS1→structure link was restored under its own predicate, `Atom` gained a count field, and
> `Ingredient` nodes came back under `AdductRecipe` (§1b, now resolved). The sample-spine section
> was also edited independently in the meantime and now has a different set of issues (§1a,
> rewritten below).
>
> **Updated again 2026-08-07** — Part 5, Group A worked through: bad accessions replaced
> (`NCIT`→`emi:Taxon`, `MS:1002894`→`emi:InChIKey2D`, `chebi:ion`→`CHEBI:24870`,
> `chebi:ChemicalStructure`→`emi:ChemicalStructure`), `sio:Metadata` replaced with a minted
> `enpkg:SampleMetadata`, `AdductCluster.adductType` moved to `LCMSFeature.resolvedAdduct`,
> `clusterconnectivity`/`Atom.count`/`hasAdduct` renamed for consistency, and the
> `ExtractSample↔LCMSAnalysis` direction question was resolved by changing the **code** to match
> the diagram — `enpkg:hasLabProcess` is now declared and emitted in
> [serializer.py](../enpkg/monolith/rdf/serializer.py), replacing the non-existent
> `emi:hasSample`. Details inline below; Part 5's Group A checkboxes are ticked.
>
> **Updated a third time 2026-08-07** — Group B decided: `MAIN_SCHEMA.mmd` **is the target model**.
> The molecular network layer, OTT match, `inTaxon`, provenance/`owl:sameAs`, and the per-feature
> cluster fields (`hasRowId`, `clusterRole`, `inAdductCluster`) were added to the diagram to match
> what the live serializer already emits — see §1d. NPC/`ChemicalTaxonAnnotation` was deliberately
> left out (never implemented in code, so not a restoration).
>
> **Updated a fourth time 2026-08-07** — Group C done: `Analysis` gained `operator`/`instrument`;
> `SampleMetadata` gained `sample_name`, `collection_date`, `collection_location`,
> `extraction_method`, `extraction_solvent` (all sourced from the user metadata file). This
> surfaced a bigger consolidation: `GeneralParams.polarity` (a pipeline config field, separate from
> `Analysis.ionization_mode`) was renamed to `ionization_mode` across the whole codebase — config,
> 5 enhancers, the GUI, tests, docs — with backward compatibility for existing saved YAML configs.
> See Part 5 Group C for the full account, including what was deliberately *not* renamed
> (`AnnotatedSpectrum.polarity`, a different, bool-typed concept).
>
> **Updated a fifth time 2026-08-07** — Group D done: `emi:hasMassiveDOI` now emits a `URIRef`
> (new `MASSIVE` namespace, `metadata.massive_id` → the MassIVE dataset URL), matching EMI's own
> `owl:ObjectProperty` declaration — verified against `EMI-vocab.owl`'s worked example rather than
> assumed. Confirmed with the user: an unset `massive_id` means the dataset isn't on MassIVE yet,
> so no triple is emitted (not an error, not a placeholder). Groups A–D are now all done; only
> Group F (namespace registration) has no open dependency.
>
> **Updated a sixth time 2026-08-07** — Group F mostly settled. `enpkg` kept as the namespace name
> despite being a legacy artifact (it's the one constant users' separately-generated KGs all share
> — see [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)); D2 decided (hand-authored TTL); the w3id.org redirect
> target confirmed as `llegregam/enpkg_full` (the actual `origin`, not the `enpkg/enpkg_full` org
> `pyproject.toml` names but that isn't configured as a remote here). Filing the actual
> `perma-id/w3id.org` PR is deliberately deferred to after Phase 2, since the target
> (`docs/vocab/enpkg.ttl`) doesn't exist yet. D3 is the only fully open item left in Group F.
>
> **Updated a seventh time 2026-08-07** — Group E done. `enpkg:hasSiriusAnnotation` retired from
> the live serializer (now `emi:hasAnnotation`, matching the diagram — implemented, tests updated,
> 173 passing), `ExternalChemicalRef`/`ExternalTaxonRef` confirmed as diagram-only (the code
> already does real per-authority `owl:sameAs`), and the full 64-term merged inventory is now
> written out under Group E — effectively completing Part 4's Phase 0 ahead of schedule. Groups
> A–F are all done or settled; Phase 2 (author `docs/vocab/enpkg.ttl` from that inventory) is the
> next concrete step.
>
> **Updated an eighth time 2026-08-10** — Phase 2 done: [docs/vocab/enpkg.ttl](vocab/enpkg.ttl)
> authored, 78 terms, verified to parse and to cover all 54 live-serializer terms. A careful
> attribute-by-attribute pass (not just trusting the Group E table) caught real gaps: two whole
> classes (`SpectralAnnotation`/`SiriusAnnotation`) were nearly left out of the first draft, and
> eight terms had never been itemized at all. `adductNeutralMass` needed an actual design decision
> (worked through with the user) plus a small code change (`ChemicalAdduct.neutral_mass`), not just
> a TTL entry; `hasCandidateStructure` turned out to have been decided in §1b but never wired into
> the serializer — fixed alongside it. 134 tests passing. D1's redirect target is now unblocked
> (the file it points to exists) but not yet pushed, so the URL doesn't resolve yet. Next up: Phase
> 4 (drift test), then Phase 3 (wire the TTL into the serializer, replacing the `_declare_*`
> methods).
>
> **Updated a ninth time 2026-08-10** — Phase 4 done:
> [test_vocabulary_drift.py](../enpkg/tests/test_data/test_vocabulary_drift.py) serializes an
> `Analysis` built to exercise every `enpkg:`-emitting branch at once (MS1 adduct cluster, MS1↔MS2
> coupling, SIRIUS, an OTT match, a molecular network with both an edge and a component, the
> gated ion layer, and the sample fields that only come from user metadata), then asserts every
> `enpkg:` IRI in the output graph is declared in `enpkg.ttl`. Verified it actually catches drift,
> not just passes vacuously: temporarily undeclared `adductNeutralMass` in the TTL and confirmed
> the test fails and names exactly that term, then restored it (byte-identical, diffed against a
> backup) — 135 tests passing. Chose an instantiated-output-graph walk over a static source-scan
> of `serializer.py`, deliberately, per the plan's own wording: a handful of `enpkg:` terms are
> minted via dict-keyed lookups (`ENPKG[local]` in `_declare_property_mappings` /
> `_declare_class_mappings`) rather than literal `ENPKG.name` attribute access, which a regex/AST
> scan would silently miss — the graph walk doesn't care how the IRI was constructed, only what
> actually got emitted. Next up: Phase 3 (wire the TTL into the serializer, replacing the
> `_declare_*` methods), then Phase 5.
>
> **Updated a tenth time 2026-08-10** — Phase 3 done, D3 decided: **inline** (the user's explicit
> call over `owl:imports` — self-contained exports matter more here than file size, and the
> w3id.org redirect isn't live yet so `owl:imports` would point at something that doesn't resolve).
> The six hand-written `_declare_*` methods and the `_PROPERTY_MAPPINGS`/`_CLASS_MAPPINGS` dicts in
> [serializer.py](../enpkg/monolith/rdf/serializer.py) are gone, replaced by one
> `_declare_vocabulary()` that parses `docs/vocab/enpkg.ttl` straight into the output graph.
> `enpkg.ttl` is now the *only* place any `enpkg:` term's domain/range/label/skos-mapping is
> spelled out — the drift test's own coverage check now doubles as a guarantee the loaded
> declarations exist, since it parses the same file. Side effect, expected and accepted: every
> exported `.ttl` now inlines the full 78-term vocabulary (341 triples, including the `[target]`
> terms not yet wired and the ontology header) rather than just the ~45 triples for terms that
> particular export happened to use — larger files, but every export is independently
> self-describing regardless of which code paths it exercised. 150 tests passing
> (`pytest enpkg/tests -m "not integration"`). Next: Phase 5 (reconcile the diagrams, write
> `VOCABULARY.md`) — the last item in Part 5.
>
> **Updated an eleventh time 2026-08-10** — Phase 5 done; **Part 5 is now fully closed.**
> [VOCABULARY.md](VOCABULARY.md) written in the `*_ENHANCER.md` walkthrough style (mint-vs-reuse
> policy, the entailment traps and why domains/ranges carry the value, the candidate-vs-confirmed
> decision, the two-speed `[live]`/`[target]` split, and how the TTL reaches the graph).
>
> The diagram question was **re-decided rather than executed as written.** Phase 5 said "decide
> which is canonical and mark the other superseded" — but that framing predates Group B making
> `MAIN_SCHEMA.mmd` the *target* model. The two now answer different questions, so both were given
> explicit role headers instead: `MAIN_SCHEMA.mmd` = target, `RDF_KG_DATA_MODEL.md` = as-built,
> each cross-linking the other and naming `enpkg.ttl` as the term authority. Marking the as-built
> doc "superseded" would have been actively misleading — it is accurate, and it is the only place
> documenting what an export actually contains.
>
> Three things this pass fixed or found:
> - **`RDF_KG_DATA_MODEL.md` was stale** on the change made earlier the same day: it still showed
>   MS1 reaching structures via `emi:hasChemicalStructure`. Corrected to
>   `enpkg:hasCandidateStructure`, with `adductNeutralMass` added to the MS1 box and the reasoning
>   written into the prose.
> - **The `[live]`/`[target]` tags were incomplete** — all 69 properties carried one, none of the 9
>   classes did. Tagged all nine; `enpkg:Ingredient` turned out to be `[target]`, not `[live]`: the
>   serializer mints ingredient *nodes* and hangs literals off them but never stamps the `rdf:type`,
>   so the class itself is not emitted. Now 54 `[live]` + 24 `[target]` = 78, none untagged.
> - **A second drift test** now asserts the tags mean what they say — `[live]` exactly when the
>   serializer emits the term, in *both* directions. Verified the two sets are identical (54 = 54,
>   no discrepancy either way). This matters because the two-diagram split above rests entirely on
>   those tags being accurate. Also re-verified the original drift test still catches an undeclared
>   term *after* Phase 3 changed how declarations enter the graph — the earlier check was run under
>   the old architecture and no longer proved anything about the new one. 151 tests passing.
>
> Not touched, flagged instead: `docs/RDF_KG_DATA_MODEL.html` is an untracked standalone viewer
> holding a hand-copied third copy of the diagram, stale by several rounds (it still shows
> `emi:hasSample`, and predates SIRIUS and the adduct clusters). Left alone deliberately — it is
> untracked, unreferenced by any doc, and whether to regenerate or delete it is the user's call.
>
> Part 4 is the implementation plan for writing out the `enpkg:` vocabulary.

---

## Part 1 — What the new schema changes

### 1a. Sample spine: restructured, then edited twice more

The sample spine has moved three times since the first pass. Current state:

```
RawMaterial["emi:RawMaterial"] --sosa:isSampleOf--> Taxon["emi:Taxon"]
ExtractSample["emi:ExtractSample"] --sosa:isSampleOf--> RawMaterial
ExtractSample --hasSampleMetadata--> SampleMetadata["enpkg:SampleMetadata"]
ExtractSample --enpkg:hasLabProcess--> LCMSAnalysis
```

**Resolved:**

- The `sosa:isSampleOf` direction bug and the missing `RawMaterial → Taxon` link — the chain now
  reads `ExtractSample -isSampleOf-> RawMaterial -isSampleOf-> Taxon`, matching EMI's own
  documentation of the intended chaining. The redundant custom `hasRawMaterial` edge is gone too.
- `Taxon["NCIT:C14250(Organism)"]` → `Taxon["emi:Taxon"]`. EMI does ship both `Organism` and
  `Taxon`; `Taxon` was picked to match what the live code already types these nodes as
  (`_add_taxon` sets `RDF.type EMI.Taxon`) — no more competing "organism" terms in the graph.
- `SampleMetadata["sio:Metadata"]` → `SampleMetadata["enpkg:SampleMetadata"]`. `sio:` was never
  vendored or verified; minted instead, matching the policy used everywhere else in the graph
  (reuse when a term fits, mint when it doesn't). It's still a distinct node from the code's
  `analysis_metadata_uri` (which is really just an alias for the `ExtractSample` node) — that gap
  is unchanged, see below.
- **The direction at the top of the spine — fixed by changing the code, not the diagram.**
  `ExtractSample --enpkg:hasLabProcess--> LCMSAnalysis` is now what
  [serializer.py](../enpkg/monolith/rdf/serializer.py) emits: `emi:hasSample` (which never existed
  in EMI — Part 3 item 1) is gone, replaced by a newly declared `enpkg:hasLabProcess`
  (`owl:ObjectProperty`, domain `emi:ExtractSample`, range `emi:LCMSAnalysis`,
  `_declare_lab_process_property` in the serializer). The diagram was treated as the target shape;
  the code caught up to it.

Still open:

- **`SampleMetadata` still needs its own URI minter.** [uris.py](../enpkg/monolith/rdf/uris.py) has
  `analysis_metadata_uri`, but that mints a per-run URI that `_add_sample` types `EMI.ExtractSample`
  — there's still no code-side node distinct from `ExtractSample` for the new `enpkg:SampleMetadata`
  class to attach to.
- **Only `sampleId` and `sampleType` are backed by the data model.** Checked directly against
  [sample_metadata.py](../enpkg/monolith/data/sample_metadata.py): it has `sample_id` and
  `sample_type`, but **no** `sample_name`, `collection_date`, or `collection_location` field at
  all — so `sampleName`, `collectionDate`, `collectionLocation` aren't just unmapped, they don't
  exist upstream yet. Same story for `LCMSAnalysis.operator`/`instrument`/`polarity` (code has
  `ionization_mode`, not `polarity`, and no operator/instrument) and
  `ExtractSample.extractionMethod`/`extractionSolvent` (no equivalent anywhere in
  [enpkg/monolith/data/](../enpkg/monolith/data/)). Tracked as a concrete to-do in Part 5, Group C
  — this is upstream of any vocabulary decision.

### 1b. Chemistry: MS1's structure link — removed, then restored with its own predicate

This was the headline change in the first pass. It is now resolved:

```
first pass (2026-08-05):  AdductAnnotation --emi:hasMolecularFormula--> MolecularFormula --emi:hasAtom--> Atom
                           (no path to ChemicalStructure at all)
current:                   AdductAnnotation --enpkg:hasCandidateStructure--> ChemicalStructure   (restored)
                           AdductAnnotation --emi:hasMolecularFormula--> MolecularFormula --enpkg:hasAtom--> Atom
```

**Resolved:** `AdductAnnotation` reaches `ChemicalStructure` again, through a *distinct* minted
predicate (`enpkg:hasCandidateStructure`) rather than the `emi:hasChemicalStructure` that MS2/SIRIUS
use. That is a deliberate choice, not a plain reversion: MS1 candidates are mass-coincidence hits (an
adduct can carry several isobaric LOTUS candidates via `adduct.lotus`), while MS2/SIRIUS structure
links represent an actual identification (fragmentation match / in-silico structure). A single shared
predicate would let a naive consumer treat both the same way — e.g. a non-halogenated compound
observed as a `[M+Cl]⁻` mass coincidence would be indistinguishable from a genuinely chlorinated,
MS2-confirmed one. Keeping the two predicates as siblings (not one `rdfs:subPropertyOf` the other)
over the *same* `ChemicalStructure` class — rather than minting a separate `CandidateStructure`
class — preserves the node-dedup-by-InChIKey design used everywhere else in the graph (see
[uris.py `CompoundURIs.lotus_uri`](../enpkg/monolith/rdf/uris.py)), since "candidate vs. confirmed"
is a property of *how an edge reached the node*, not an intrinsic property of the compound itself.

This restores the three consequences flagged in the first pass:

1. `enpkg:hasCorrespondingAdduct` is justified again — the compound it points at is reachable from
   the MS1 side.
2. The LOTUS provenance chain (`inTaxon`, `wasDerivedFrom`) has an entry point from MS1 again — but
   only once those edges are added back to the diagram; **neither appears anywhere in the current
   file** (see §1d — still unresolved, independent of this fix).
3. `annotationRank` / `annotationScore` on `AdductAnnotation` are interpretable again — the
   candidate compounds actually being scored are visible in the graph.

**Also resolved:** `AdductRecipe` has its `Ingredient` nodes back
(`AdductRecipe --enpkg:hasIngredient--> Ingredient`, with `ingredientName` / `ingredientCount`),
matching `_add_recipe`'s per-ingredient IRIs in the live code. "Decision C" in
[RDF_DATA_MODEL.md](RDF_DATA_MODEL.md) no longer has a live divergence to resolve. `adductFormula`
stays alongside it as the flat rendered form (`"[M+H]+"`) — a reasonable *addition* mirroring
`_format_adduct`'s output, not a replacement for the structured recipe.

**New since the restore:** `Atom` gained a `+count: int` field, and its incoming edge is now
`enpkg:hasAtom` rather than `emi:hasAtom` (EMI has no such term — see Part 2 — so minting it is
correct, not a gap). This closes the modeling issue flagged in the first pass: without a count,
`Atom` couldn't reconstruct stoichiometry (C₁₅H₂₀O₆ vs. "contains C, H, O somewhere") and added
little over the `formula` string it hangs off. ChEBI has no instance-level "atom with count"
property to reuse for this — the only candidate, `obo:BFO_0000051` (`has_part`), appears a handful
of times in ChEBI itself as a class-level existential restriction with no cardinality — so minting
`enpkg:hasAtom` was the right call.

### 1c. Annotations

| | Old / code | New |
|---|---|---|
| SIRIUS attach predicate | `enpkg:hasSiriusAnnotation` | `emi:hasAnnotation` (unified) |
| MS2 → structure | `emi:hasChemicalStructure` → **InChIKey2D node** | → `ChemicalStructure` **and** `emi:hasInchiKey2D` → InchiKey2D |
| SIRIUS → structure | → InChIKey2D node | same doubling |
| MS1 attributes | — | `+annotationMethod`, `+adductNeutralMass` (was `adductMass`) |

Unifying SIRIUS under `emi:hasAnnotation` is a genuine simplification and worth taking —
`enpkg:hasSiriusAnnotation` exists only for historical reasons, and all three classes already
subclass `emi:StructuralAnnotation`.

### 1d. What the schema was missing — resolved 2026-08-07, `MAIN_SCHEMA.mmd` confirmed as the target model

Open question 2 is answered: `MAIN_SCHEMA.mmd` is the target model, not a focused view. Everything
below was therefore added back, matching what's already implemented in the live serializer:

- **The molecular network layer** — `LFpair` (`+hasCosine`, `+hasMassDifference`, linked to
  `LCMSFeature` via `emi_hasFirstMember`/`emi_hasSecondMember`) and `FBMNComponent`
  (`+componentSize`, linked `LCMSFeatureSet -> FBMNComponent -> LCMSFeature` via
  `enpkg_hasNetworkComponent`/`enpkg_hasComponentMember`/`emi_hasFBMNComponent`) — new "Molecular
  Network Relation Subgraph" block. ([NETWORK_ENHANCER.md](NETWORK_ENHANCER.md) §6)
- **`OTTMatch`** (typed `emi:Taxon`, like the code) carrying the match-quality attributes
  (`matchScore`, `isApproximateMatch`, `isSynonym`, `nomenclatureCode`, `searchString`), linked
  `LCMSAnalysis -> OTTMatch` via `enpkg_hasOTTMatch`.
- **`BibliographicResource`** (`+identifier`), linked from `ChemicalStructure` via
  `prov_wasDerivedFrom`.
- **`owl:sameAs` external authorities**, represented as two placeholder classes matching
  [RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md)'s convention rather than modeling Wikidata /
  PubChem / GBIF / NCBITaxon each as their own class: `ExternalChemicalRef` (from
  `ChemicalStructure`) and `ExternalTaxonRef` (from `Taxon` and `OTTMatch`).
- **`emi:inTaxon`**, from `ChemicalStructure` and from `SpectralAnnotation` — both point at the
  existing `Taxon` class, no new node needed. (`SiriusAnnotation` doesn't carry `inTaxon` in the
  live code either, so it wasn't added there — not an oversight.)
- **`hasRowId` and `clusterRole`**, added to `LCMSFeature`; `inAdductCluster`, added as the
  `LCMSFeature -> AdductCluster` back-link (`enpkg_inAdductCluster`) alongside `hasAnchor`/
  `hasClusterMember`.

**Deliberately still excluded: NPC / `emi:ChemicalTaxonAnnotation`.** This one is different in
kind from everything else on this list — it's not something the diagram dropped, it's something
the *live serializer* never implemented either (D3 in
[DATA_MODEL_AND_SERIALIZATION_GAP.md](DATA_MODEL_AND_SERIALIZATION_GAP.md), still just a roadmap
item). Adding it to the diagram now would be net-new design work, not restoring something that
already exists — left out pending an explicit call on that.

If the new diagram is meant as *"the target model"* rather than *"a subset view"*, these are
deletions and should be deliberate. If it is a focused view, add a note saying so — otherwise it
will be read as the spec.

### 1e. Minor / cosmetic — all resolved

- ~~`AdductCluster` carries `+adductType`.~~ Moved to `LCMSFeature.resolvedAdduct` — matches the
  live code, where `enpkg:resolvedAdduct` is a per-member literal (`"[M+Na]+"`), not a cluster-level
  one.
- ~~`clusterconnectivity` casing~~ → `clusterConnectivity`, matching its neighbors and
  `enpkg:clusterConnectivity` as emitted.
- ~~`Ion` class body formatting~~ (`+` markers added, trailing comma removed) — this class also had
  a leftover mermaid syntax error (a stray double `{ {`) from an earlier manual edit, fixed at the
  same time.
- ~~`hasAdduct` as an attribute name~~ → `adductForm` on both `AdductAnnotation` and
  `SiriusAnnotation`, matching the noun-phrase convention every other attribute uses.

---

## Part 2 — Term IDs that do not check out

Every external accession in the new schema, checked against [vocab/](vocab/). **Status as of the
Part 5 / Group A cleanup — most of the ❌ rows below are now resolved in the diagram; kept for the
record of what was wrong and what replaced it.**

| In the schema (original) | Verdict |
|---|---|
| ~~`MS:1002894(Inchikey2D)`~~ | ✅ **Resolved** — was `InChIKey` (the *full* key, not 2D) misapplied as a node type; replaced with plain `emi:InChIKey2D` (what the code uses; no PSI-MS 2D accession exists to cite). |
| ~~`chebi:ion`~~ | ✅ **Resolved** — not a real accession; replaced with `CHEBI:24870(Ion)`. |
| ~~`chebi:ChemicalStructure`~~ | ✅ **Resolved** — ChEBI has no such class; replaced with `emi:ChemicalStructure` (what the code uses). |
| ~~`NCIT:C14250(Organism)`~~ | ✅ **Resolved** — replaced with `emi:Taxon`, matching what the live code already types these nodes as. No more competing "organism" terms. |
| ~~`sio:Metadata`~~ (on `SampleMetadata`) | ✅ **Resolved** — `sio:` was never vendored/verified; replaced with a minted `enpkg:SampleMetadata`. |
| `MS:1000866(MolecularFormula)` | ⚠️ **Still open.** Exists ("molecular formula") but is a cvParam **value** class. The cvParam note in [RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md) §4 says to reify or `skos:exactMatch` these, not use them as node types — not addressed this round. |
| `CHEBI:33250(Atom)` | ✅ correct, unchanged. |
| `emi:RawMaterial` | ✅ exists, unchanged. |
| `emi:hasMolecularFormula`, `hasSampleMetadata` (the predicates) | ❌ **Still open.** Neither exists in EMI (or anywhere) as declared — need minting as `enpkg:hasMolecularFormula` / `enpkg:hasSampleMetadata`, or a real source. (`hasAtom`, `hasRawMaterial`, `hasExtractSample` from the original list are resolved — see §1a/§1b; `extractionMethod`/`extractionSolvent` are literal attributes gated on Part 5 Group C's data-model work, not predicates needing a mint.) |

### Entailment traps

**`emi:hasInChIKey2D` has `rdfs:domain emi:InChIKey` and is an `owl:FunctionalProperty`.** The new
schema uses it from `SpectralAnnotation` and `SiriusAnnotation`, which entails *those annotations
are InChIKeys*, and functionality means at most one per annotation — two SIRIUS candidates would get
`owl:sameAs`-merged. Use a second `emi:hasChemicalStructure` edge instead: its range is
`emi:ChemicalStructure`, and `emi:InChIKey2D ⊑ emi:ChemicalStructure`, so pointing it at the 2D node
is valid — which is exactly what the code does today. This is the same class of bug the network doc
caught with `hasFBMNComponent`.

The good news: `emi:LCMSFeature ⊑ emi:MS2Spectrum` and
`emi:StructuralAnnotation ⊑ emi:SpectrumAnnotation`, so `emi:hasAnnotation` (domain `MS2Spectrum`,
range `SpectrumAnnotation`) is clean on features for all three annotation channels.

---

## Part 3 — Live bugs in the current serializer

Surfaced while checking the schema; unrelated to it.

1. ~~`emi:hasSample` does not exist in EMI.~~ **Fixed.** It emitted on every analysis
   ([serializer.py](../enpkg/monolith/rdf/serializer.py) — line number shifted since this was
   written). EMI has no sample-linking property at all — its own documentation blurb says the
   analysis "uses (`prov:used`/`sosa:hasFeatureOfInterest`) some sample". Rather than switch to
   `sosa:hasFeatureOfInterest`, the diagram's own answer was adopted instead: `enpkg:hasLabProcess`
   is now declared (`owl:ObjectProperty`, domain `emi:ExtractSample`, range `emi:LCMSAnalysis`, in
   `_declare_lab_process_property`) and emitted `ExtractSample -> LCMSAnalysis` — matching
   `MAIN_SCHEMA.mmd`'s direction, which was treated as authoritative over the old code. See §1a.
2. **`emi:hasMassiveDOI` is an `owl:ObjectProperty`** (domain `LCMSAnalysis`, and EMI's own example
   gives it an IRI object). [serializer.py:307](../enpkg/monolith/rdf/serializer.py#L307) emits it as
   a literal via `_set`. Should be `URIRef("https://massive.ucsd.edu/...")`. Still open.
3. Minor: `ChemicalStructure --emi:hasInChIKey2D--> …` entails the compound node is an
   `emi:InChIKey`. Semi-defensible since the IRI *is* `identifiers.org/inchikey/<full>`, but EMI
   would have you type it `emi:InChIKey` (which is `⊑ ChemicalStructure`, so it can carry both
   types).

---

## Part 4 — Plan: writing out the ENPKG vocabulary

### The situation

The serializer uses **53 distinct `enpkg:` terms** (was 52 — `hasLabProcess` was just added,
replacing the invalid `emi:hasSample`, Part 3 item 1). Only **14** are declared in-graph, across
six ad-hoc `_declare_*` methods. The other **39 appear as bare IRIs with no type, label, domain, or
range** — a consumer loading an export sees predicates with no definition anywhere.

Declared today (14):

| Where | Terms |
|---|---|
| `_PROPERTY_MAPPINGS` | `adductMass`, `charge`, `productIonMz`, `productIonIntensity`, `lowIntensityThreshold` |
| `_CLASS_MAPPINGS` | `AdductAnnotation`, `SpectralAnnotation`, `SiriusAnnotation` |
| `_declare_adduct_cluster_class` | `AdductCluster` |
| `_declare_correspondence_property` | `hasCorrespondingAdduct` |
| `_declare_lab_process_property` | `hasLabProcess` |
| `_declare_fbmn_component_terms` | `hasNetworkComponent`, `hasComponentMember`, `componentSize` |

Undeclared today (39): `AdductRecipe`, `annotationRank`, `annotationScore`,
`classyfireKingdom`, `classyfireSuperclass`, `classyfireClass`, `classyfireDirectParent`,
`clusterConnectivity`, `clusterCountCoverage`, `clusterIntensityCoverage`, `clusterRole`,
`hasAdductCluster`, `hasAnchor`, `hasClusterMember`, `hasIngredient`, `hasIon`, `hasOTTMatch`,
`hasRecipe`, `hasSiriusAnnotation`, `inAdductCluster`, `ingredientCount`, `ingredientName`,
`isApproximateMatch`, `isPositive`, `isSynonym`, `manualValidation`, `matchScore`,
`maxIonsPerSpectrum`, `multimerFactor`, `nMatchedPeaks`, `nomenclatureCode`, `resolvedAdduct`,
`sampleFilenameNeg`, `sampleFilenamePos`, `searchString`, `sourceId`, `stereocentersTotal`,
`stereocentersUnspecified`, `xlogp`.

There is no `enpkg.ttl` anywhere in the repo.

### Three decisions to make first

**D1 — the namespace.** [namespaces.py:19](../enpkg/monolith/rdf/namespaces.py#L19) flags
`https://w3id.org/enpkg#` as an IRI we do not control. Publishing terms under an unregistered IRI is
the one thing that is expensive to undo. Registering a w3id redirect is a PR to
`perma-id/w3id.org`; EMI already lives there, so precedent and probably contacts exist.

✅ **Name confirmed 2026-08-07: keep `enpkg`.** It's a legacy name from the pipeline's first
version, but renaming a published vocabulary prefix defeats the same reuse goal that makes the
vocabulary worth stabilizing in the first place — see [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

✅ **Redirect target confirmed: `llegregam/enpkg_full` on GitHub** (the repo actually configured
as `origin` — checked via `git remote -v`, `main` branch confirmed via `git symbolic-ref
refs/remotes/origin/HEAD`), not the `enpkg/enpkg_full` org listed in `pyproject.toml`'s
`homepage`/`repository` fields, which isn't configured as a remote here and may not hold this code.
Concretely: `w3id.org/enpkg#` → `https://raw.githubusercontent.com/llegregam/enpkg_full/main/docs/vocab/enpkg.ttl`.

⏸ **Filing the actual `perma-id/w3id.org` PR is blocked on Phase 2** — the target above 404s until
`docs/vocab/enpkg.ttl` exists. Decide-now-file-later: the target is locked in, but there's nothing
to redirect to yet. Also worth knowing before filing: `raw.githubusercontent.com` serves `.ttl`
files as `text/plain`, not `text/turtle` — every tool actually used in this pipeline parses Turtle
by content regardless, so this doesn't block anything now, but a stricter external consumer doing
HTTP content negotiation might care later (GitHub Pages with a configured `content-type` would fix
it, if that's ever worth the setup).

**D2 — source of truth: authored TTL, or Python registry?** ✅ **Decided 2026-08-07: Option A,
hand-authored `docs/vocab/enpkg.ttl`**, with a thin Python-side registry (names only, no
domain/range) purely to drift-test the serializer against it. Confirmed important precisely
*because* the ontology must be reused unchanged across every user's separately-generated KG (see
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)) — under a Python-source-of-truth model the "ontology" reshapes itself
on every unrelated code edit; under a hand-authored TTL it's a deliberate, independently-versioned
artifact, the same relationship the pipeline already has with EMI's own `EMI-vocab.owl`.

**D3 — do term declarations belong in every data export?** Today they do: every per-run `.ttl`
carries the ~40 declaration triples. Once there is a published ontology, data graphs should
reference it (`owl:imports`) rather than inline it. Worth deciding now because it changes what
`AnalysisSerializer.__init__` does.

### Phases

**Phase 0 — freeze the inventory.** Extract the 52 terms mechanically from the serializer,
cross-check against the "Custom `enpkg:` vocabulary to define" section of
[RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md) (which is stale — it predates the cluster,
network, and SIRIUS work). Output: a table of term · kind · domain · range · definition · external
mapping. This doubles as the review checklist.

**Phase 1 — resolve "should this be minted at all?" per term.** Several current mints are
avoidable, and this is the cheapest time to catch them:

- `enpkg:hasSiriusAnnotation` → drop in favour of `emi:hasAnnotation` (the new schema already
  proposes this).
- `enpkg:charge` → PSI-MS `MS:1000041` is already `skos:exactMatch`-linked; keep the mint, that
  pattern is correct.
- `enpkg:classyfire*` → ChemOnt IRIs exist; the mapped doc already defers this. Decide: literals
  forever, or `owl:sameAs` to ChemOnt.
- Of the original eight new-schema terms, three are already settled (`hasAtom`, and `hasRawMaterial`
  /`hasExtractSample` were dropped — §1a) and `Ion` resolved to a real ChEBI class rather than a
  mint. Left to decide: `hasMolecularFormula`, `hasSampleMetadata`, `extractionMethod`,
  `extractionSolvent` (the last two are literal attributes gated on Part 5 Group C's data-model
  work, not urgent).

**Terms already decided during the schema review (ready to transcribe in Phase 2, no re-derivation
needed):**

- `enpkg:hasCandidateStructure` — `owl:ObjectProperty`, domain `enpkg:AdductAnnotation`, range
  `emi:ChemicalStructure`. Sibling of `emi:hasChemicalStructure`, deliberately *not* declared
  `rdfs:subPropertyOf` it — keeps "mass-coincidence candidate" and "confirmed identification"
  queryable apart (§1b).
- `enpkg:hasAtom` — `owl:ObjectProperty`, domain the `MS:1000866`-typed `MolecularFormula` node,
  range the minted `enpkg:Atom`. No ChEBI/PSI-MS instance-level equivalent exists (checked — Part
  2 / §1b).
- `enpkg:Atom` — `owl:Class`, `skos:closeMatch CHEBI:33250`, carrying `enpkg:element` and a count
  property. Note: the diagram currently labels the count field bare `+count`, while the sibling
  `Ingredient` class uses the fully-qualified `+ingredientCount` — worth renaming to
  `enpkg:atomCount` for the same reason `ingredientCount` isn't just `count`: once transcribed into
  the TTL, an unqualified `enpkg:count` would be reused (accidentally or not) across unrelated
  classes.
- `enpkg:hasIngredient` / `enpkg:ingredientName` / `enpkg:ingredientCount` — unchanged from the
  live serializer's `_add_recipe`; the diagram now matches, nothing new to decide.
- `enpkg:hasLabProcess` — `owl:ObjectProperty`, domain `emi:ExtractSample`, range
  `emi:LCMSAnalysis`. Already declared and emitted in the live serializer
  (`_declare_lab_process_property`) — replaces the invalid `emi:hasSample`; not just decided but
  implemented (§1a, Part 3 item 1).
- `enpkg:SampleMetadata` — `owl:Class`. Minted in place of the unverified `sio:Metadata`. Still
  needs a URI minter in [uris.py](../enpkg/monolith/rdf/uris.py) before it's implemented, not just
  diagrammed (§1a).

**Phase 2 — author `docs/vocab/enpkg.ttl`.** Ontology header (IRI, version IRI,
`owl:imports emi:`, creator, license), then every term with `rdf:type`, `rdfs:label`,
`rdfs:comment`, `rdfs:domain`, `rdfs:range`, and `skos:exactMatch`/`closeMatch` where an external
term corresponds. Group it by the same regions as the schema: spine · adduct/MS1 · annotation
ranking · cluster · network · compound scalars · taxonomy match · ions.

Domains and ranges are where the value is — they are what makes the entailment traps in Part 2
impossible to reintroduce, and every one of the 39 undeclared terms currently has none.

**Phase 3 — wire it in.** Replace the five `_declare_*` methods with either an `owl:imports`
reference (D3 = separate) or a single load-from-TTL (D3 = inline). Either way the declarations stop
being Python string literals.

**Phase 4 — a drift test.** One test that walks the serializer's output graph, collects every IRI in
the `enpkg:` namespace, and asserts each is defined in `enpkg.ttl`. This is what stops the 13-of-52
situation from recurring; it would have caught `emi:hasSample` too if extended to check EMI terms
against the vendored OWL — worth doing, it is the same walk.

**Phase 5 — reconcile the diagrams.** [RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md) is currently the
accurate one (modulo the network layer, which is newer than it). Once `MAIN_SCHEMA.mmd` is
corrected, decide which is canonical and mark the other superseded — the pattern already exists in
[RDF_DATA_MODEL.md](RDF_DATA_MODEL.md)'s header. Then a `VOCABULARY.md` walkthrough in the
`*_ENHANCER.md` style, explaining the mint-vs-reuse policy and the entailment reasoning.

### Suggested order

D1 in parallel (it has external latency) → Phase 0 → Phase 1 → Phase 2 → Phase 4 → Phase 3 →
Phase 5.

Phase 4 before Phase 3 deliberately: write the test against the current serializer, watch it fail on
39 terms, and let it go green as the TTL is wired in.

---

## Open questions

1. ~~Does `AdductAnnotation → MolecularFormula` replace or supplement the link to
   `ChemicalStructure`?~~ **Resolved (§1b):** supplements — both edges are kept, via distinct
   predicates (`enpkg:hasCandidateStructure` vs. `emi:hasMolecularFormula`).
2. ~~Is `MAIN_SCHEMA.mmd` the target model or a focused view?~~ **Resolved 2026-08-07: target
   model.** The network layer, OTT match, `inTaxon`, provenance, and the per-feature cluster fields
   were added back — see §1d. One item deliberately left out: NPC / `emi:ChemicalTaxonAnnotation`,
   since unlike everything else on that list, it was never implemented in the live serializer
   either — adding it now would be new design work, not restoring an omission.
3. ~~Is `Taxon --hasRawMaterial--> RawMaterial` meant as the formal inverse of
   `RawMaterial --sosa:isSampleOf--> Taxon`, or a replacement for it?~~ **Resolved (§1a):** the
   custom edge was dropped entirely — the diagram now uses a single, correctly-oriented
   `sosa:isSampleOf` chain for both hops.

---

## Part 5 — Consolidated action plan for a complete vocabulary

Everything raised in Parts 1–4, organized as one checklist. Grouped so you can see what's
independent (start anytime) versus what's gated on a decision.

### Group A — Diagram fixes (cheap; mostly renames and relabels) — ✅ done 2026-08-07

- [x] `ExtractSample --enpkg_hasLabProcess--> LCMSAnalysis` vs. code's `LCMSAnalysis
      --emi:hasSample--> ExtractSample` — **resolved by changing the code**, not the diagram. The
      diagram's direction/predicate was taken as the target shape; `serializer.py` now declares and
      emits `enpkg:hasLabProcess` (`ExtractSample -> LCMSAnalysis`), retiring the invalid
      `emi:hasSample`. (§1a, Part 3 item 1)
- [x] `Taxon["NCIT:C14250(Organism)"]` vs. `emi:Organism`/`emi:Taxon` — confirmed EMI ships both;
      switched to `emi:Taxon`, matching what the live code already types these nodes as. (Part 2)
- [x] `SampleMetadata["sio:Metadata"]` — dropped the unverified external type; minted
      `enpkg:SampleMetadata` instead. (Still needs a URI minter to be implemented, not just
      diagrammed — see §1a "still open".) (§1a, Part 2)
- [x] `MS:1002894(Inchikey2D)` — wrong accession dropped; now plain `emi:InChIKey2D`. (Part 2)
- [x] `chebi:ion` / `chebi:ChemicalStructure` — `Ion` now cites the real `CHEBI:24870`;
      `ChemicalStructure` now `emi:ChemicalStructure`. A leftover mermaid syntax error on the `Ion`
      class from an earlier manual edit was also caught and fixed here. (Part 2)
- [x] `AdductCluster.adductType` moved to `LCMSFeature.resolvedAdduct` — matches the live code,
      where it's a per-member literal, not a cluster-level one. (§1e)
- [x] `clusterconnectivity` → `clusterConnectivity` (casing). (§1e)
- [x] `Atom.count` → `atomCount`, matching `Ingredient.ingredientCount`'s naming convention. (Part 4,
      "terms already decided")
- [x] Cosmetic: `Ion` class body formatting (`+` markers, trailing comma), `hasAdduct` → `adductForm`
      on both annotation classes. (§1e)

### Group B — One scope decision (blocks how far Group A/E go)

- [x] **Is `MAIN_SCHEMA.mmd` the target model or a focused view?** (Open question 2) **Target
      model.** The network layer, OTT `Match`, `inTaxon`, provenance, and the per-feature cluster
      fields (§1d) are now in the diagram. NPC / `ChemicalTaxonAnnotation` deliberately excluded —
      never implemented in the live serializer either, so it's new design work, not a restoration.

### Group C — Data-model work — ✅ done 2026-08-07

- [x] `Analysis`: added `operator: Optional[str]`, `instrument: Optional[str]`.
- [x] **`polarity` vs. `ionization_mode` resolved codebase-wide, not just on `Analysis`.** Turned
      out `GeneralParams.polarity` (a separate config field driving 5 enhancers + the GUI) was the
      same concept under a different name — renamed to `ionization_mode` everywhere (config,
      enhancers, GUI, tests, docs), with a `model_validator` accepting the old `polarity` key so
      existing saved YAML configs (confirmed real ones exist in the gitignored `gui_workspace/`)
      keep loading. Diagram's `LCMSAnalysis.polarity` → `ionizationMode` to match. Left alone:
      `AnnotatedSpectrum.polarity` — a per-spectrum derived `bool` (charge sign), a different
      concept from the run-level `"pos"/"neg"` string, not a rename target.
- [x] `SampleMetadata` ([sample_metadata.py](../enpkg/monolith/data/sample_metadata.py)): added
      `sample_name`, `collection_date`, `collection_location`, `extraction_method`,
      `extraction_solvent` (the last two back the RDF `ExtractSample` node's attributes — there's
      no separate `ExtractSample` Python class, `SampleMetadata` covers both). All five sourced
      from the user-supplied metadata file, same path as `sample_id`/`sample_type` today.
      Not yet wired into the RDF serializer (no predicate names decided for `operator`/
      `instrument`/`extractionMethod`/etc. — that's Group A/E/G territory, not this group).
- Test coverage added: `test_ionization_mode_accepts_legacy_polarity_key`,
  `test_from_dict_roundtrip_legacy_polarity_key`,
  `test_from_dict_recognizes_sample_and_extraction_fields`. Full suite run: 184 passed (5 errors
  are a pre-existing, unrelated missing-fixture-file issue — confirmed via `git log` to predate
  this session).

### Group D — Live serializer bugs — ✅ done 2026-08-07

- [x] `emi:hasSample` doesn't exist in EMI — fixed as part of Group A item 1: replaced with
      `enpkg:hasLabProcess`, direction flipped to match the diagram.
- [x] `emi:hasMassiveDOI` is emitted as a literal but is declared `owl:ObjectProperty` in EMI.
      Fixed: a new `MASSIVE` namespace
      (`https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=`) was added to
      [namespaces.py](../enpkg/monolith/rdf/namespaces.py), verified against EMI's own
      `vann:example` on `hasMassiveDOI` in `EMI-vocab.owl` (lines 3101–3113) rather than assumed.
      `_massive_uri` in [serializer.py](../enpkg/monolith/rdf/serializer.py) builds the URI from
      the bare accession (`metadata.massive_id`, e.g. `"MSV000087728"`) and emits nothing at all
      when it's unset — confirmed with the user this is the correct behavior: no `massive_id`
      means the dataset hasn't been uploaded to MassIVE yet, not an error. A value that's already
      a full URL is kept as-is rather than double-prefixed (defensive; no real example on hand to
      confirm the field is always a bare accession). Tests:
      `test_massive_doi_is_a_uriref_not_a_literal`, `test_massive_doi_keeps_a_full_url_as_is`,
      `test_massive_doi_absent_when_massive_id_unset`.

### Group E — Reconcile the two vocabularies in play — ✅ done 2026-08-07

Two decisions closed this out, then the merged inventory (below) is the deliverable Phase 0 was
waiting on:

- [x] **`hasSiriusAnnotation` retired.** The one real code/diagram conflict — implemented, not
      just decided. [serializer.py](../enpkg/monolith/rdf/serializer.py)'s `_add_sirius_annotations`
      now emits `emi:hasAnnotation` (matching MS1/MS2 and the diagram), dropping the dedicated
      `enpkg:hasSiriusAnnotation` predicate entirely. A consumer now tells the three annotation
      channels apart by `rdf:type` (`enpkg:AdductAnnotation`/`SpectralAnnotation`/`SiriusAnnotation`),
      the same way it already had to for MS1 vs. MS2. Updated alongside:
      [test_serializer.py](../enpkg/tests/test_data/test_serializer.py) (both SIRIUS tests +
      a new regression test asserting the old predicate never appears),
      [RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md), [SIRIUS_ENHANCER.md](SIRIUS_ENHANCER.md),
      [DATA_MODEL_AND_SERIALIZATION_GAP.md](DATA_MODEL_AND_SERIALIZATION_GAP.md) (which also had
      the pre-Group-A `hasSample` edge still showing — fixed in passing). 173 tests pass.
- [x] **`ExternalChemicalRef`/`ExternalTaxonRef` (§1d) stay documentation-only.** They were added
      to the diagram as a visual simplification, mirroring how
      [RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md) draws external authorities as one generic
      "targets" box rather than four separate ones. The live code already does the more precise
      thing — real per-authority `owl:sameAs` to `WD`/`PUBCHEM`/`GBIF`/`NCBITAXON`, each its own
      established namespace in [namespaces.py](../enpkg/monolith/rdf/namespaces.py). Minting new
      `enpkg:ExternalChemicalRef`/`ExternalTaxonRef` classes for Phase 2 would be a regression
      from what's already implemented — they don't get inventory rows below.

#### The merged term inventory

Legend: ✅ declared in code · 🟡 emitted, undeclared (Phase 2 needs to declare it) · 🔵 in the
diagram / Python data model, not yet wired into the serializer · Kind: **C** class,
**OP** object property, **DP** datatype property.

**Spine & sample**

| Term | Kind | Status | Notes |
|---|---|---|---|
| `hasLabProcess` | OP | ✅ | `ExtractSample → LCMSAnalysis`. Replaces invalid `emi:hasSample`. |
| `hasOTTMatch` | OP | 🟡 | `LCMSAnalysis → Taxon` (OTT match instance, typed `emi:Taxon`). |
| `sourceId` | DP | 🟡 | On `ExtractSample`. |
| `sampleFilenamePos` / `sampleFilenameNeg` | DP | 🟡 | On `ExtractSample`. |
| `matchScore` / `isApproximateMatch` / `isSynonym` / `nomenclatureCode` / `searchString` | DP | 🟡 | OTT match-quality literals. |
| `SampleMetadata` | C | 🔵 | Minted, replacing unverified `sio:Metadata` (§1a). No URI minter in `uris.py` yet. |
| `hasSampleMetadata` | OP | 🔵 | `ExtractSample → SampleMetadata`, currently unprefixed in the diagram — needs `enpkg:` before Phase 2. |
| `sampleName` / `collectionDate` / `collectionLocation` | DP | 🔵 | On `SampleMetadata`; Python fields exist (`sample_metadata.py`), no predicate names chosen. |
| `extractionMethod` / `extractionSolvent` | DP | 🔵 | On `ExtractSample`; Python fields exist, no predicate names chosen. |
| `operator` / `instrument` | DP | 🔵 | On `LCMSAnalysis`; Python fields exist (`Analysis.operator`/`.instrument`), no predicate names chosen. |
| `ionizationMode` (literal) | DP | 🔵 | Diagram shows it as a literal; code currently only encodes it via the `LCMSAnalysisPos`/`Neg` subclass, not also as a literal. Phase 2 should decide: subclass-only (current), literal-only, or both. |
| `RawMaterial` (minter) | — | 🔵 | Class exists in EMI and is used in the diagram; no URI minter in `uris.py` (§1a). |

**Adduct / MS1**

| Term | Kind | Status | Notes |
|---|---|---|---|
| `AdductAnnotation` | C | ✅ | `⊑ emi:StructuralAnnotation`. |
| `AdductRecipe` | C | 🟡 | |
| `AdductCluster` | C | ✅ | `skos:closeMatch emi:FBMNComponent`. |
| `adductMass` | DP | ✅ | `skos:exactMatch MS:1003243`. |
| `charge` | DP | ✅ | `skos:exactMatch MS:1000041`. |
| `hasRecipe` | OP | 🟡 | `AdductAnnotation → AdductRecipe`. |
| `hasIngredient` | OP | 🟡 | `AdductRecipe → Ingredient`. |
| `ingredientName` / `ingredientCount` | DP | 🟡 | On the `Ingredient` blank-node-equivalent. |
| `isPositive` / `multimerFactor` | DP | 🟡 | On `AdductRecipe`. |
| `hasAdductCluster` / `hasAnchor` / `hasClusterMember` / `inAdductCluster` | OP | 🟡 | Cluster ↔ feature wiring. |
| `clusterConnectivity` / `clusterIntensityCoverage` / `clusterCountCoverage` | DP | 🟡 | mzAdan CGC/CIC/CCC indices, on `AdductCluster`. |
| `clusterRole` / `resolvedAdduct` | DP | 🟡 | Per-member literals on `LCMSFeature`. |
| `hasCandidateStructure` | OP | 🔵 | `AdductAnnotation → ChemicalStructure`. Decided in this session (§1b) — sibling of `emi:hasChemicalStructure`, not a subproperty. Not yet implemented in the serializer. |
| `hasMolecularFormula` | OP | 🔵 | `AdductAnnotation → MolecularFormula`; `MolecularFormula` itself is `MS:1000866` (a cvParam value class used as a node type — Part 2's still-open item). |
| `hasAtom` | OP | 🔵 | `MolecularFormula → Atom`. Decided this session (§1b) — no ChEBI/PSI-MS instance-level equivalent exists. |
| `Atom` | C | 🔵 | `skos:closeMatch CHEBI:33250`; carries `element` + `atomCount` (renamed from bare `count` this session). |

**Annotation ranking (cross-channel) & SIRIUS**

| Term | Kind | Status | Notes |
|---|---|---|---|
| `SpectralAnnotation` / `SiriusAnnotation` | C | ✅ | Both `⊑ emi:StructuralAnnotation`. |
| `annotationRank` / `annotationScore` | DP | 🟡 | Stamped per-channel by `_add_ranked_annotations` / SIRIUS's own rank. |
| `hasCorrespondingAdduct` | OP | ✅ | `SpectralAnnotation → AdductAnnotation`. |
| `nMatchedPeaks` | DP | 🟡 | On `SpectralAnnotation`. |

**Molecular network**

| Term | Kind | Status | Notes |
|---|---|---|---|
| `hasNetworkComponent` / `hasComponentMember` | OP | ✅ | `LCMSFeatureSet ↔ FBMNComponent ↔ LCMSFeature`. |
| `componentSize` | DP | ✅ | On `FBMNComponent`. |
| *(`LFpair`, `hasFirstMember`/`hasSecondMember`, `hasCosine`, `hasMassDifference`, `FBMNComponent`, `hasFBMNComponent`)* | — | — | All real EMI terms, reused as-is — nothing to mint. |

**Compound scalars & structure**

| Term | Kind | Status | Notes |
|---|---|---|---|
| `xlogp` / `stereocentersTotal` / `stereocentersUnspecified` / `manualValidation` | DP | 🟡 | On `ChemicalStructure`. |
| `classyfireKingdom` / `Superclass` / `Class` / `DirectParent` | DP | 🟡 | ChemOnt ranks kept as literals (node-linking deferred, per `RDF_DATA_MODEL_mapped.md`). |

**Ions (gated, `include_ions`)**

| Term | Kind | Status | Notes |
|---|---|---|---|
| `hasIon` | OP | 🟡 | `LCMSFeature → Ion` (blank/unnamed-equivalent node). |
| `productIonMz` / `productIonIntensity` | DP | ✅ | `skos:exactMatch MS:1001225`/`MS:1001226`. |
| `lowIntensityThreshold` | DP | ✅ | `skos:exactMatch MS:1000629`. |
| `maxIonsPerSpectrum` | DP | 🟡 | Reproducibility stamp. |

**Totals (superseded by Phase 2, below):** this table's 64 undercounted — a careful attribute-by-
attribute cross-check against the diagram while authoring the TTL found 8 more real terms this
table missed entirely (see Phase 2).

---

### Phase 2 — ✅ `docs/vocab/enpkg.ttl` authored, 2026-08-10

[docs/vocab/enpkg.ttl](vocab/enpkg.ttl) now exists: **78 declared terms** (9 classes, 16 object
properties, 53 datatype properties, 341 triples), verified to parse cleanly with `rdflib` and to
cover all 54 terms the live serializer emits (checked mechanically, not just by eye).

Before writing it, a full attribute-by-attribute pass against `MAIN_SCHEMA.mmd` (not just trusting
the table above) turned up real gaps the merged inventory had missed:

- **Two classes never made it into the first TTL draft** — `enpkg:SpectralAnnotation` and
  `enpkg:SiriusAnnotation` (only `enpkg:AdductAnnotation` got written). Caught by mechanically
  diffing the TTL's declared terms against the live serializer's, not by re-reading — this is
  exactly the class of mistake Phase 4's drift test exists to catch automatically.
- **Eight terms with no inventory row at all**, all diagram attributes that were mentioned in
  prose (§1b, §1e) but never itemized: `adductFormula` (on `AdductRecipe` — the flat `"[M+H]+"`
  form, `skos:exactMatch MS:1002813`), `formula` (on `MolecularFormula`), `element` / `isotope` /
  `atomCount` (on `Atom` — its own attributes were listed in the *notes* column, never as rows),
  `annotationMethod` (on `AdductAnnotation`), `algorithm` (on `SpectralAnnotation`), `siriusVersion`
  (on `SiriusAnnotation`). All now declared `[target]` in the TTL; `algorithm`/`siriusVersion`
  aren't backed by the Python data model yet (same situation `operator`/`instrument` were in before
  Group C).
- **`adductNeutralMass` — resolved through discussion, then implemented, not just declared.**
  `enpkg:adductMass` (already live) is the *ion* mass; the diagram's `adductNeutralMass` asked for
  something different — the underlying neutral molecule's mass. Resolved as
  `lotus[0].structure_exact_mass` (the matched candidate's own mass — already the input to
  `compute_adduct_mass`, so the two fields are mutually consistent: `adductMass ==
  recipe.compute_adduct_mass(adductNeutralMass)`), not the alternative considered
  (`recipe.compute_neutral_mass(observed_mz)`, a measurement-derived diagnostic, a different
  quantity). Implemented: `ChemicalAdduct.neutral_mass` (new property,
  [adduct_class.py](../enpkg/monolith/data/ms1_data_classes/adduct_class.py)) and
  `enpkg:adductNeutralMass` now emitted in
  [serializer.py](../enpkg/monolith/rdf/serializer.py)'s `_add_chemical_adduct`, `skos:closeMatch
  chemrof:monoisotopic_mass`. Test: `test_adduct_carries_neutral_and_ion_mass`.
- **`enpkg:hasCandidateStructure` was decided in §1b but never actually wired into the code** —
  `_add_chemical_adduct` was still emitting the shared `emi:hasChemicalStructure` for MS1
  candidates. Fixed alongside the above (same function). Test:
  `test_ms1_candidate_structure_uses_sibling_predicate`.
- **`hasRecipe` vs. `hasAdductRecipe`** — the diagram's edge label never matched the live,
  already-tested `enpkg:hasRecipe`. Confirmed keeping `hasRecipe`; diagram fixed to match.
- **`MolecularFormula`'s class identity fixed** — was typed directly as `MS:1000866`, the PSI-MS
  *value* class Part 2 already flagged as unsuited for that (§ "Term IDs that do not check out").
  Now `enpkg:MolecularFormula`, `skos:exactMatch ms:1000866` — resolves that open item.
- **`hasSampleMetadata` given its `enpkg:` prefix** in the diagram (was bare, flagged in Group E).

All four `test_serializer.py` additions pass; full suite: **134 passed**.

---

### Group F — Namespace & governance (Part 4, D1–D3) — mostly settled 2026-08-07

- [x] Namespace **name** confirmed: `enpkg` (kept, despite being a legacy name — see
      [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)).
- [x] Redirect **target** confirmed: `https://raw.githubusercontent.com/llegregam/enpkg_full/main/docs/vocab/enpkg.ttl`
      (not the `enpkg/enpkg_full` org in `pyproject.toml` — not an actual remote here).
- [ ] **Filing the `perma-id/w3id.org` PR itself.** `docs/vocab/enpkg.ttl` exists as of Phase 2
      (2026-08-10), but only locally/uncommitted — the redirect target won't actually resolve
      until it's committed and pushed to `main` on `llegregam/enpkg_full`. Once pushed, filing the
      external PR is still a distinct, explicit action to take when ready, not done here.
- [x] Decide TTL-as-source-of-truth vs. Python registry (D2) — **decided: Option A**,
      hand-authored `docs/vocab/enpkg.ttl`, Python side validates against it.
- [x] Decide whether declarations are inlined per export or `owl:imports`ed (D3) — **decided:
      inline.** Self-contained exports outweigh file size for this use case, and the w3id.org
      redirect isn't live yet so `owl:imports` would point at an unresolvable IRI today. Group F is
      now fully done except for actually filing the `perma-id/w3id.org` PR.

### Group G — Author & wire the vocabulary

Execute Part 4's Phase 0 → 1 → 2 → 4 → 3 → 5.

- [x] **Phase 0 (freeze the inventory)** — done, superseded by Phase 2's 78-term `enpkg.ttl` (the
      Group E table undercounted by 8; the TTL is now the authoritative inventory).
- [x] **Phase 1 (mint-or-reuse calls)** — done alongside Phase 0/2; every term's decision is
      recorded in its `rdfs:comment` in the TTL.
- [x] **Phase 2 (author `docs/vocab/enpkg.ttl`)** — done, see above.
- [x] **Phase 4 (drift test)** — done:
      [test_vocabulary_drift.py](../enpkg/tests/test_data/test_vocabulary_drift.py). Verified it
      actually catches drift (temporarily undeclared a term, watched it fail and name it, restored
      it), not just that it passes.
- [x] **Phase 3 (wire it in)** — done. The five (six, counting the class/property-mapping split)
      `_declare_*` methods and their backing dicts are gone; `_declare_vocabulary()` parses
      `docs/vocab/enpkg.ttl` into the graph once per `AnalysisSerializer`. `enpkg.ttl` is now the
      only place any `enpkg:` term is defined.
- [x] **Phase 5 (reconcile the diagrams / write `VOCABULARY.md`)** — done.
      [VOCABULARY.md](VOCABULARY.md) written; both diagrams given explicit role headers (target vs.
      as-built) rather than one being marked superseded — see the eleventh status entry for why
      that departs from this phase's original wording.

### Suggested sequence across all of it

1. ~~Group A (diagram cleanup)~~ — **done.**
2. ~~Group B (scope decision)~~ — **done.** Target model.
3. ~~Group C (data-model fields)~~ — **done.**
4. ~~Group D (live serializer bugs)~~ — **done.**
5. ~~Group E (reconcile + merged inventory)~~ — **done.** `hasSiriusAnnotation` retired in code
   (matching the diagram), `ExternalChemicalRef`/`ExternalTaxonRef` confirmed documentation-only,
   and the 64-term merged inventory above now stands in for Phase 0.
6. ~~Group F~~ — **done.** Name, D2, and D3 all decided (D3: inline). Redirect target decided and
   unblocked (`enpkg.ttl` exists) — filing the PR is a separate explicit action, not yet taken.
7. ~~Phase 0 / 1 / 2~~ — **done.** `docs/vocab/enpkg.ttl` authored (78 terms), all live-serializer
   terms confirmed covered, `hasSampleMetadata`'s prefix and the `adductNeutralMass` question both
   resolved along the way (the latter needed a code change, not just a vocabulary decision).
8. ~~Phase 4 (drift test)~~ — **done.** `test_vocabulary_drift.py` walks a serialized output graph
   (not a static source-scan — a handful of terms are minted via dict-keyed `ENPKG[local]` lookups
   a regex/AST scan would miss) and asserts every emitted `enpkg:` IRI is declared in the TTL.
9. ~~Phase 3 (wire it in)~~ — **done.** `serializer.py`'s `_declare_vocabulary()` parses
   `docs/vocab/enpkg.ttl` directly; the six hand-written `_declare_*` methods are gone.
10. ~~Phase 5 (reconcile the diagrams / `VOCABULARY.md`)~~ — **done.** Both diagrams given explicit
    roles (target vs. as-built), neither superseded; `VOCABULARY.md` written.

**Part 5 is complete.** What remains is not part of this plan:

- **Filing the `perma-id/w3id.org` PR** — needs `docs/vocab/enpkg.ttl` committed and pushed to
  `main` first (it is still uncommitted), then it is a distinct external action.
- **`ionizationMode`'s literal-vs-subclass-only question** — still genuinely open; declared
  `[target]` in the TTL either way.
- **Wiring the 24 `[target]` terms** into the serializer, as and when each is wanted. Two of them
  (`algorithm`, `siriusVersion`) need Python data-model fields before they can be emitted at all.
- **`docs/RDF_KG_DATA_MODEL.html`** — untracked, stale, a hand-copied third copy of the diagram.
  Regenerate or delete; left untouched pending a decision.
