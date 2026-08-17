# Plan: Couple MS2 annotations to their corresponding MS1 adducts in the RDF graph (D2)

> **Status:** planned, not yet implemented. Updated to reflect the MS1 cluster dispatch and
> MS2 adduct gate that landed after the first draft.

## Context

Today the RDF serializer emits a feature's MS1 adduct hypotheses and MS2 spectral-library
matches as **disconnected** sets: `_add_spectrum` calls `_add_ranked_annotations` once for MS1
(top-k mass-based adducts) and once for MS2 (fragmentation matches), and nothing links them. An
MS2 match identifies a *specific compound* by fragmentation; the MS1 adduct that proposes that
*same compound* is the one that explains how it ionised to land at this precursor. Every other
MS1 adduct on that feature is a mass coincidence with no fragmentation support.

**Goal:** when a feature has MS2 annotation(s), keep only the MS1 adducts whose candidate
structures include an MS2-identified compound, and add an explicit link from each MS2 annotation
to its corresponding adduct node. Features with no MS2 are untouched.

### How the recent enhancer changes shape this (new since the first draft)

Two things now hold by the time serialization runs, and they make D2 *cleaner*, not different:

- **MS2 adduct gate** (`ms2_adduct_gating.select_spectra_for_ms2`, default `non_satellite`):
  MS2 annotation only runs on **anchors + singletons** (base ions). So every MS2-bearing feature
  is an anchor or a singleton — never a satellite.
- **MS1 cluster dispatch** (`ms1_cluster_dispatch.inherit_satellite_annotations`): anchors and
  singletons carry their **full** precursor-mass LOTUS search (the rich candidate set D2 filters);
  satellites carry only the single inherited molecule, re-cast under their own form.

Net: D2's correspondence always runs on a feature (anchor/singleton) that holds the **full**
`ms1_annotations` set — exactly what we want to prune against MS2. Satellites, having no MS2, fall
through D2's "no MS2 → unchanged" branch and keep their (small) inherited MS1 set. **No special
satellite handling is needed in the serializer.**

### Decisions
- **Correspondence = compound-level (2D InChIKey), all adduct forms.** An MS1 adduct corresponds
  to an MS2 annotation iff some LOTUS entry in the adduct's formula group shares the MS2
  annotation's short (14-char) InChIKey. Keep *every* adduct form that proposes that structure.
- **Features with no MS2 → unchanged** (current MS1 top-k behaviour). This includes satellites.
- **Serializer-only, keep the adduct node + link to it.** In-memory `Analysis` is untouched; the
  MS1 `AdductAnnotation` node stays (recipe, mass, structures) and the MS2 annotation gains a link
  to it. Lowest risk.
- **Per-feature, not cluster-propagated (scope boundary).** The correspondence links MS2 → MS1 on
  the *same feature* only. We do **not**, in D2, propagate an anchor's MS2 confirmation outward to
  its satellites' inherited adducts (i.e. no "MS2 confirmed X on the anchor, so mark the satellite's
  X-adduct corresponding too"). That is a coherent future step (cluster-level MS2 support) but it is
  a separate concern from the per-feature MS1/MS2 disconnect this plan fixes, and the `AdductCluster`
  node (D1) + `enpkg:resolvedAdduct` already make the anchor↔satellite molecule identity queryable.
  Recorded here so the boundary is explicit rather than silent.

### Ground truth (re-verified against current `serializer.py`)
- `_add_ranked_annotations` (L328-362) returns `None`; the empty-input branch returns early. Both
  call sites — MS1 (L296-301) and MS2 (L302-308) — are in `_add_spectrum` (L283-312) and ignore the
  return, so making it return its emitted list is non-breaking.
- `_add_chemical_adduct` (L404-422) and `_add_ms2_annotation` (L571-598) mint the nodes;
  `_declare_class_mappings` (L173-182) / `_declare_property_mappings` (L163-171) show the
  minted-term declaration pattern to copy for the new property.
- `ChemicalAdduct.lotus` is a **formula group** — a list of `Lotus` that can hold several *distinct*
  2D structures (same formula, different connectivity). Correspondence must scan **all** entries'
  `short_inchikey`, not just `adduct.short_inchikey` (which is `lotus[0]` only).
- `MS2ChemicalAnnotation.short_inchikey` (chemical_annotation.py L67) is the 14-char key;
  `Lotus.short_inchikey` (lotus_class.py L202, `structure_inchikey[:14]`) is directly comparable.
- No unit-test file for the serializer today; the existing serializer coverage lives in
  `enpkg/tests/test_data/test_serializer.py` (fast, DB-free) — D2 tests go there. End-to-end
  verification is via `smoke_serialize.py`.

## Changes (all in `serializer.py`)

**1. `_add_ranked_annotations` returns what it emitted.** Change it to return
`list[tuple[score, annotation, annotation_uri]]` (append `(score, annotation, annotation_uri)` in
the emit loop; the early `return` for empty input returns `[]`). Both existing call sites can keep
ignoring it — non-breaking.

**2. Declare a new object property `enpkg:hasCorrespondingAdduct`.** Add a declaration in the style
of `_declare_class_mappings` (a new `_declare_correspondence_property`, or fold into an existing
declare): `rdf:type owl:ObjectProperty`, `rdfs:label "corresponding MS1 adduct hypothesis"`,
`rdfs:domain enpkg:SpectralAnnotation`, `rdfs:range enpkg:AdductAnnotation`. Emitted once per graph
from `__init__`.

**3. Restructure `_add_spectrum`'s annotation emission** (the spine + parent-mass/RT/area triples
at L289-293 stay as-is; only the two `_add_ranked_annotations` calls at L296-308 change):
   - Emit **MS2 first** via `_add_ranked_annotations`; from the returned list build
     `ms2_by_short_ik: dict[str, list[URIRef]]` (short InChIKey → MS2 annotation URIs).
   - **If `ms2_by_short_ik` is non-empty** (feature has serialized MS2 annotations):
     - Select corresponding adducts from the **full** `spectrum.ms1_annotations`:
       ```python
       corresponding = [a for a in spectrum.ms1_annotations
                        if {l.short_inchikey for l in a.lotus} & ms2_by_short_ik.keys()]
       ```
     - Emit them with `_add_ranked_annotations(..., top_k=None, ...)` — keep *all* corresponding,
       still rank/score-stamped for consistency. Capture the returned `(score, adduct, uri)`.
     - For each emitted adduct, for each of its short InChIKeys present in `ms2_by_short_ik`, add
       `(ms2_uri, ENPKG.hasCorrespondingAdduct, adduct_uri)` for every MS2 URI under that key.
   - **Else** (no MS2, incl. satellites): emit MS1 exactly as today —
     `_add_ranked_annotations(uri, spectrum.ms1_annotations, scores=(ms1…), top_k=self.top_k_ms1, …)`.
   - SIRIUS (L309) and ions (L310-311) are unchanged.

   Behaviour to honour: on MS2-bearing features the `top_k_ms1` cap **no longer applies** (all
   corresponding adducts kept, drawn from the *full* set, not the capped one); a feature with MS2
   but zero corresponding adducts emits **no** MS1 node (fine — "only those that latch");
   correspondence is computed against the **serialized** (top-k) MS2 set, so links only ever point
   at MS2 nodes that exist.

**4. Docs:** add `enpkg:hasCorrespondingAdduct` and the MS2-gated MS1-pruning rule to
[RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md) and the diagram/legend in
[RDF_KG_DATA_MODEL.md](RDF_KG_DATA_MODEL.md) (the `SpectralAnnotation -.-> AdductAnnotation` edge is
already sketched as "NEW" in the gap doc's diagram). Update
[DATA_MODEL_AND_SERIALIZATION_GAP.md](DATA_MODEL_AND_SERIALIZATION_GAP.md) D2 → IMPLEMENTED.

**5. (Optional) smoke metric:** in `smoke_serialize.py` `_info`, add a soft count of MS2
annotations carrying `enpkg:hasCorrespondingAdduct`.

## Tests — `enpkg/tests/test_data/test_serializer.py` (extend, fast/DB-free)

Reuse the existing conftest fixtures (`make_spectrum`, `make_adduct`, `make_recipe`, `make_lotus`,
`make_analysis`) plus a small MS2-annotation builder (see `make_sirius_annotation` for the pattern;
add an inline `MS2ChemicalAnnotation` if no fixture exists). Cases:
- **Latching + link:** a feature with an MS2 match on compound X and MS1 adducts where one formula
  group contains X → only X-containing adduct(s) serialized, each MS2 annotation carries
  `enpkg:hasCorrespondingAdduct` → that adduct URI, and the mass-coincidence adducts are gone.
- **All forms kept:** X present under two recipes (`[M+H]+` and `[M+Na]+`) → both adduct nodes kept
  and both linked (compound-level, all forms).
- **No corresponding adduct:** MS2 on a compound absent from every MS1 group → no MS1 adduct node
  emitted for that feature, no dangling `hasCorrespondingAdduct`.
- **No MS2 (incl. satellite):** feature with no MS2 → MS1 emitted exactly as top-k today; assert the
  adduct count matches the pre-D2 behaviour.
- **Property declared once + idempotency:** `enpkg:hasCorrespondingAdduct` typed `owl:ObjectProperty`
  with domain/range; re-serializing the same analysis adds no duplicate triples.

## Verification

1. `poetry run ruff check enpkg/monolith/rdf/serializer.py enpkg/tests/test_data/test_serializer.py`
   → clean.
2. `poetry run python -m pytest enpkg/tests -m "not integration" -q` → green (new coupling tests +
   everything else unchanged).
3. **Serialize real data:** `python -m enpkg.monolith.rdf.smoke_serialize --batch-dir <dir-with-analysis.pkl>`
   → existing checks pass; inspect a `.ttl`.
4. **SPARQL — links exist & MS1 pruned on MS2 features:**
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
5. **No-MS2 features unchanged:** pick a feature with no `SpectralAnnotation`; confirm its MS1 adduct
   count still equals the old top-k behaviour.
6. **Shared 2D structure:** confirm the corresponding adduct's structures include the MS2 compound:
   `?adduct emi:hasChemicalStructure/emi:hasInChIKey2D ?ik2d . ?ms2 emi:hasChemicalStructure ?ik2d .`
