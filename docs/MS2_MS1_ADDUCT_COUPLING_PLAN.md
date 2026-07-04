# Plan: Couple MS2 annotations to their corresponding MS1 adducts in the RDF graph

> **Status:** planned, not yet implemented.

## Context

Today the RDF serializer emits a feature's MS1 adduct hypotheses and MS2 spectral-library
matches as **disconnected** sets: MS1 dumps its top-k mass-based adducts, MS2 separately lists
fragmentation matches, and nothing links them. An MS2 match identifies a *specific compound*
by fragmentation; the MS1 adduct that proposes that *same compound* is the one that explains
how it ionised to land at this precursor. Every other MS1 adduct on that feature is a mass
coincidence with no fragmentation support.

**Goal:** when a feature has MS2 annotation(s), keep only the MS1 adducts whose candidate
structures include an MS2-identified compound, and add an explicit link from each MS2
annotation to its corresponding adduct node. Features with no MS2 are untouched.

### Decisions
- **Correspondence = compound-level (2D InChIKey), all adduct forms.** An MS1 adduct
  corresponds to an MS2 annotation iff some LOTUS entry in the adduct's formula group shares
  the MS2 annotation's short (14-char) InChIKey. Keep *every* adduct form that proposes that
  structure.
- **Features with no MS2 → unchanged** (current MS1 top-k behaviour).
- **Serializer-only, keep the adduct node + link to it.** In-memory `Analysis` is untouched;
  the MS1 `AdductAnnotation` node stays (recipe, mass, structures) and the MS2 annotation
  gains a link to it. Lowest risk.

### Ground truth (read during planning)
- [serializer.py](../enpkg/monolith/rdf/serializer.py): `_add_spectrum` (L242-270) calls `_add_ranked_annotations` (L286-320) once for MS1, once for MS2; `_add_chemical_adduct` (L362-380) and `_add_ms2_annotation` (L481-508) mint the nodes; `_declare_class_mappings`/`_declare_property_mappings` (L139-158) show the pattern for declaring minted terms.
- `ChemicalAdduct.lotus` is a **formula group** — a list of `Lotus` that can contain several *distinct* 2D structures (same molecular formula, different connectivity). So correspondence must scan **all** entries' `short_inchikey`, not just `adduct.short_inchikey` (which is `lotus[0]` only — adduct_class.py L106-109).
- `MS2ChemicalAnnotation.short_inchikey` is the 14-char key (chemical_annotation.py L83-85); `Lotus.short_inchikey` is `structure_inchikey[:14]` (lotus_class.py L194-197) — directly comparable.
- No unit-test file for the serializer; verification is via [smoke_serialize.py](../enpkg/monolith/rdf/smoke_serialize.py).

## Changes (all in serializer.py)

**1. `_add_ranked_annotations` returns what it emitted.** Change it to return a
`list[tuple[score, annotation, annotation_uri]]` (currently returns `None`; the empty-input
branch returns `[]`). Both existing call sites can ignore it, so this is non-breaking.

**2. Declare a new object property `enpkg:hasCorrespondingAdduct`.** Add a small declaration
(reuse the style of `_declare_class_mappings`): `rdf:type owl:ObjectProperty`, an `rdfs:label`
("corresponding MS1 adduct hypothesis"), `rdfs:domain enpkg:SpectralAnnotation`,
`rdfs:range enpkg:AdductAnnotation`. Emitted once per graph.

**3. Restructure `_add_spectrum`'s annotation emission** (the spine + parent-mass/RT/area
triples stay as-is):
   - Emit **MS2 first** via `_add_ranked_annotations`; from the returned list build
     `ms2_by_short_ik: dict[str, list[URIRef]]` (short InChIKey → MS2 annotation URIs).
   - **If `ms2_by_short_ik` is non-empty** (feature has serialized MS2 annotations):
     - Select corresponding adducts:
       `corresponding = [a for a in spectrum.ms1_annotations
                         if {l.short_inchikey for l in a.lotus} & ms2_by_short_ik.keys()]`
     - Emit them with `_add_ranked_annotations(..., top_k=None, ...)` (keep *all* corresponding;
       still rank/score-stamped for consistency). Capture the returned `(score, adduct, uri)`.
     - For each emitted adduct, for each matching short InChIKey, add
       `(ms2_uri, ENPKG.hasCorrespondingAdduct, adduct_uri)` for every MS2 URI under that key.
   - **Else** (no MS2): emit MS1 exactly as today —
     `_add_ranked_annotations(uri, spectrum.ms1_annotations, scores=(ms1…), top_k=self.top_k_ms1, …)`.

   Behaviour notes to honour: on MS2-bearing features the MS1 `top_k_ms1` cap **no longer
   applies** (all corresponding adducts are kept, drawn from the *full* `ms1_annotations`, not
   the capped set); a feature with MS2 but zero corresponding adducts emits no MS1 node (fine —
   "only those that latch"); correspondence is computed against the **serialized** (top-k) MS2
   set so links only ever point at nodes that exist.

**4. Docs:** add `enpkg:hasCorrespondingAdduct` and the MS2-gated MS1 pruning rule to
[docs/RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md) and the data-model diagram in
[docs/RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md).

**5. (Optional) smoke metric:** in [smoke_serialize.py](../enpkg/monolith/rdf/smoke_serialize.py)
`_info`, add a soft count of MS2 annotations carrying `enpkg:hasCorrespondingAdduct`.

## Verification

1. **Serialize real data:** `python -m enpkg.monolith.rdf.smoke_serialize --batch-dir <dir-with-analysis.pkl>` → all existing checks still pass; inspect a `.ttl`.
2. **SPARQL — links exist & MS1 is pruned on MS2 features:**
   ```sparql
   PREFIX emi:<https://w3id.org/emi#>  PREFIX enpkg:<https://w3id.org/enpkg#>
   SELECT ?feature ?ms2 ?adduct WHERE {
     ?feature emi:hasAnnotation ?ms2 , ?adduct .
     ?ms2 a enpkg:SpectralAnnotation . ?adduct a enpkg:AdductAnnotation .
     ?ms2 enpkg:hasCorrespondingAdduct ?adduct .
   }
   ```
   Cross-check with `FILTER NOT EXISTS`: on an MS2-bearing feature there should be **no**
   `AdductAnnotation` lacking an incoming `hasCorrespondingAdduct`.
3. **No-MS2 features unchanged:** pick a feature with no `SpectralAnnotation`; confirm its MS1
   adduct count still equals the old top-k behaviour.
4. Confirm the corresponding adduct's structures include the MS2 compound (shared 2D node):
   `?adduct emi:hasChemicalStructure/emi:hasInChIKey2D ?ik2d . ?ms2 emi:hasChemicalStructure ?ik2d .`
