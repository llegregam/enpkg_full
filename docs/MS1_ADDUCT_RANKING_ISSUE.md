# MS1 adduct ranking: why feature 543 is annotated `[M+H+2Na]3+` instead of `[M+H]+`

## TL;DR

For feature **543** (precursor m/z **181.0495**) in `actea_EtOAc-1_pos`, the KG's
top‑ranked MS1 adduct is **`[M+H+2Na]3+`** and there is **no `[M+H]+`** at all.
The chemically obvious and *correct* answer is **C9H8O4** (exact mass 180.04226)
as **`[M+H]+`** (= 181.0495, an exact match), and it is independently corroborated
by the feature's own SIRIUS and MS2/ISDB annotations.

**The adduct-string formatter is not at fault.** `[M+H+2Na]3+` is the faithful
rendering of the recipe `{proton:1, sodium:2, charge:3}`. The real problem is that
**MS1 candidate ranking has no notion of adduct or charge plausibility**: it ranks
candidates purely by how well a *compound's* natural‑product‑class fingerprint
matches a taxonomy‑weighted consensus, then keeps only the top 5. The correct
`[M+H]+` compound scored low on that taxonomy/chemistry criterion, fell outside the
top‑5 window, and was dropped — while an implausible triply‑charged di‑sodium
adduct of an unrelated ~496 Da compound won rank #1.

---

## 1. The symptom

Feature 543: `emi:hasParentMass 181.0495`, RT 3.458 min, area 2.66e4.
Its five serialized MS1 adduct annotations:

| rank | `hasAdduct`     | `adductMass` | implied neutral mass M | compound (short InChIKey) |
|-----:|-----------------|-------------:|-----------------------:|---------------------------|
| 1    | `[M+H+2Na]3+`   | 181.052      | ~496.2                 | YWQNATCXFBWYHU            |
| 2    | `[M+3Na]3+`     | 181.0495     | ~474.2                 | INYICJIVZADXGB, …         |
| 3    | `[M+Mg]2+`      | 181.0437     | ~338.1                 | LQGRWSKECPQNES            |
| 4    | `[M+H+Na]2+`    | 181.0497     | ~338.1                 | LQGRWSKECPQNES            |
| 5    | `[M+Ca]2+`      | 181.0584     | ~282.1                 | YGFACAUGZLLRPQ            |

Every candidate is within the ±0.01 Da precursor window, so **mass alone cannot
separate them** — they are all "valid" by mass. None is `[M+H]+`.

> Read from a pre-fix export, which also suffered the adduct-URI collision described
> in §2. The five rows above each carry a *different* recipe, so they were distinct
> nodes either way — but candidates sharing a recipe with one of them would have been
> swallowed, so the top-5 window may have been narrower than it appears. Re-verify
> against a regenerated graph before acting on the exact ordering.

### Ground truth

- `[M+H]+` of **C9H8O4** (180.04226) = **181.0495** — a perfect match to the precursor.
- LOTUS *does* contain C9H8O4 in the window (25 stereo/organism variants), so the
  enhancer **did** generate this candidate.
- Feature 543's own **SIRIUS** annotations include 6 C9H8O4 InChIKeys
  (GQYBCIHRWMPOOF, HGEFWFBFQKWVMY, JXIPYOZBOMUUCA, QAIPRVGONGVQAS, SIUKXCMDYPYCLH,
  WFVBGDQDYLTEJX); the **MS2/ISDB** annotations agree.

So the correct annotation was available and corroborated, yet absent from the MS1 output.

---

## 2. What is NOT the bug

- **`_format_adduct` is correct** (`enpkg/monolith/rdf/serializer.py:181`).
  `{proton:1, sodium:2, charge:3}` → `[M+H+2Na]3+` is the right string.
- **A node never shows one recipe's mass with another's label.** `adductMass` and
  `hasAdduct` both derive from the *same* `adduct.recipe`, so that pairing is sound.

> **Correction (2026-08-10).** This section previously also claimed *"each recipe gets
> a distinct URI via `_recipe_hash`"* and concluded serialization was internally
> consistent. Each *recipe* did get a distinct URI — but that was never sufficient,
> because a hypothesis is a **(LOTUS formula group, recipe)** pairing and the group
> was absent from the key. Two different molecules proposed for one feature under one
> recipe therefore minted the **same** adduct node: `_add_chemical_adduct` early-returned
> on the second, writing no chemistry, while `_add_ranked_annotations` still stamped its
> rank and score onto the first one's node. Measured on the `actea_MeOH-1_pos` export:
> 858 / 7595 adduct nodes carried 2–5 conflicting `enpkg:annotationRank` values, 1233
> hypotheses were absorbed, 793 features lost candidates, and 122 / 1065
> `enpkg:hasCorrespondingAdduct` links pointed at an adduct proposing a *different*
> compound than the MS2 match. Fixed by adding the formula to the key
> (`uris.py::chemical_adduct_uri`); see `docs/RDF_KG_DATA_MODEL.md` for the current shape.
>
> **This is a separate defect from the ranking problem analysed here** — the two were
> present in the same graph, which is why §1's numbers were read off it. The ranking
> analysis below stands on its own, but re-derive the specific rows after regenerating.

The defect this document is about is upstream of rendering, in **which candidates
survive ranking**.

---

## 3. How MS1 candidates are generated (context)

`MS1Enhancer.enhance` (`enpkg/monolith/enhancers/ms1_enhancer.py`) attaches to each
spectrum **every** `(LOTUS formula group × ionization recipe)` whose computed ion
m/z falls within `parent_mz_tol = 0.01 Da` of the precursor. No ranking, no cap —
`spectrum.ms1_annotations` holds *all* mass‑coincident hypotheses. For a precursor
at 181.0495 that includes both the correct `[M+H]+` (C9H8O4) and dozens of exotic
multiply‑charged / metal adducts of heavier compounds that happen to coincide in m/z.

Ranking and the top‑k cut happen **later, in the serializer**.

---

## 4. The reranking pipeline

```
                         ┌──────────────────────────────────────────────┐
                         │  Each candidate compound carries 3 NPC vectors │
                         │  (from the "hammer" classifier):               │
                         │   pathways[~30], superclasses[~100s],          │
                         │   classes[~1000s]  = "what NP class is this?"  │
                         └──────────────────────────────────────────────┘
                                            │
         STAGE 1 — weights_enhancer.py:92   │  build the spectrum's CONSENSUS profile
                                            ▼
    spec_pathway = Σ_k ( taxo_sim_k × cand_pathway_k )     (same for super, class)
       where taxo_sim_k = how close candidate k's SOURCE ORGANISM is to the
       sample organism (Actaea), normalised so Σ_k taxo_sim_k = 1
                                            │
         STAGE 2 — serializer.py:294        │  score each candidate vs the consensus
                                            ▼
    align(cand) =  mean(cand_pathway · spec_pathway)
                 × mean(cand_super   · spec_super)
                 × mean(cand_class   · spec_class)
                                            │
         STAGE 3 — serializer.py:336        │  sort desc, keep top_k_ms1 = 5
                                            ▼
                        the 5 rows that end up in the KG
```

Relevant code:
- Consensus build: `WeightsEnhancer.compute_ms1_classifications`
  (`enpkg/monolith/enhancers/weights_enhancer.py:92-105`).
- Per‑candidate NPC vectors: `ChemicalAdduct.get_pathway_scores` …
  (`enpkg/monolith/data/ms1_data_classes/adduct_class.py:128-138`), taken from `lotus[0]`.
- Taxonomic closeness: `Lotus.taxonomical_similarity_with_otl_match`
  (`enpkg/monolith/data/lotus_class.py:207`).
- Alignment score + truncation: `AnalysisSerializer._alignment_score` /
  `_add_ranked_annotations` (`enpkg/monolith/rdf/serializer.py:294-342`).

---

## 5. Why `[M+H]+` (C9H8O4) was evicted

The score at Stage 2 **contains no term for adduct, charge, or mass accuracy.**
`[M+H]+` vs `[M+H+2Na]3+` is invisible to it. Ranking is decided entirely by *"does
this compound's NP‑class fingerprint match the taxonomy‑weighted consensus of the
candidate pool?"* Four factors made the correct adduct lose:

**(a) Taxonomic distance from the sample.**
The C9H8O4 compound's source organisms are taxonomically distant from *Actaea*, so
its `taxo_sim` is small. That hurts twice: it contributes almost nothing to the
consensus (Stage 1), and it aligns weakly with a consensus shaped by others (Stage 2).

**(b) Self‑reinforcing consensus.**
The consensus is *built from the candidate pool itself*, weighted by taxonomy. If
exotic‑adduct compounds from *Actaea*‑close organisms dominate the pool, the
consensus tilts toward them — and they then win the alignment they helped define.
The lone, taxonomically‑distant `[M+H]+` compound is voted out by a majority it
barely influenced.

```
   candidate pool (by taxo weight)         consensus profile         who aligns best?
   ───────────────────────────────         ─────────────────         ───────────────
   exotic adducts, Actaea-close  ██████──►  looks like "them"  ──►    ...them.
   C9H8O4 [M+H]+,  Actaea-far    ─                                    (not the answer)
```

**(c) It is a dot product, not a cosine.**
`mean(a · b)` is unnormalised — it rewards candidates whose NPC vectors are
"peakier"/more confident, regardless of *directional* agreement. A confidently
classified compound outscores a diffusely classified one even at the same adduct.

**(d) Numerical fragility + a hard top‑5 cut.**
Superclass/class vectors are large and sparse, so each `mean` is ~1e‑3 and the
triple product ~1e‑9 (hence the near‑zero `annotationScore` values in the KG:
4.7e‑9 … 3.1e‑9). Ranking is decided by a handful of high‑probability entries;
tiny consensus shifts reshuffle the order. `[M+H]+` landed at rank ≥6 and the
`top_k_ms1 = 5` truncation dropped it silently.

### One‑sentence summary

> The reranking answers *"which candidate **structure** best fits the sample's
> taxonomy/chemistry consensus,"* **not** *"which **adduct** assignment is physically
> plausible"* — so a chemically absurd triply‑charged di‑sodium adduct can top the
> list because its underlying compound sits closer to the taxonomic consensus, and
> the real `[M+H]+` is pushed out of the top‑5 window.

---

## 6. Options on the table (for discussion — no decision yet)

1. **Adduct‑plausibility prior in ranking.** Add a per‑adduct prior weight (common
   singly‑charged high; multiply‑charged / multi‑metal low) that factors into the
   MS1 ranking or acts as a strong tiebreaker. Keeps all candidates, just orders
   them sensibly. *Least destructive, most defensible.*
2. **Curate the recipe list.** Gate/drop exotic recipes (e.g. charge ≥ 3, or ≥ 2
   metal ingredients) from `POSITIVE_RECIPES` / `NEGATIVE_RECIPES` so they are never
   generated. *Simplest, but permanently loses genuine rare adducts.*
3. **Reserve top‑k slots for common adducts.** Always keep `[M+H]+` / `[M+Na]+` /
   `[M+K]+` (when present) in the serialized top‑k regardless of NPC score.
   *Surgical, but somewhat ad‑hoc.*
4. **Rework the score itself.** Replace the unnormalised triple product with a
   cosine similarity and/or fold in mass accuracy + adduct prior. *Biggest change;
   touches the research‑sensitive ranking the code already flags as unsettled
   (`serializer.py:274`).*

### Open questions for the team
- Should adduct/charge plausibility be a **prior** (soft) or a **filter** (hard)?
- Do we ever legitimately want triply‑charged / multi‑metal adducts in the KG, or
  are they always noise for small molecules?
- Should MS1 ranking be allowed to consult MS2/SIRIUS corroboration on the same
  feature (cross‑evidence), or stay independent?
- Is the unnormalised dot product intended, or should it be cosine similarity?

---

## Appendix — reproduction

- Precursor / adduct data: `gui_workspace/logs/batch_20260706_142713/actea_EtOAc-1_pos/actea_EtOAc-1_pos.ttl` (feature 543).
- LOTUS lookup: `enpkg/monolith/test-data/databases/enpkg.duckdb`, table `compounds`,
  `structure_exact_mass BETWEEN 180.0322 AND 180.0522` → C9H8O4 @ 180.04226.
- Config: `gui_workspace/my_config.yaml` (`parent_mz_tol: 0.01`, `top_to_output: 5`).
