# The MS2 Enhancer — How It Works

*A conceptual walkthrough for presentations and onboarding.*

> Companion to [MS1_ENHANCER.md](MS1_ENHANCER.md). Where the MS1 enhancer generates
> candidates from **mass alone**, the MS2 enhancer asks a stricter question using the
> molecule's **fragmentation fingerprint**.

---

## 1. What problem does it solve?

A precursor mass narrows down *what a molecule could be*, but many different molecules share
the same mass. To actually **identify** a compound, mass spectrometrists look at how it
**fragments**: when the molecule is broken apart in the instrument (MS/MS), the pattern of
fragment masses and intensities is a structural fingerprint.

The **MS2 enhancer** answers:

> *Does this feature's fragmentation spectrum match the (predicted) spectrum of a known
> natural product?*

It compares every experimental MS/MS spectrum against a large reference library of
**in-silico predicted spectra** — the **ISDB** (In-Silico DataBase) built from the LOTUS
natural-products collection — and records the good matches as candidate identifications.

Like MS1, this is still **candidate generation** — it proposes identities and a match score,
but it does not produce the final ranking. The difference is the *evidence*: MS2 candidates
are backed by fragmentation agreement, making them higher-confidence than mass-only matches.

```mermaid
%%{init: {'theme':'dark'}}%%
flowchart LR
    A["LC-MS² features<br/>(precursor m/z + MS/MS spectra)"] --> B["MS1 Enhancer<br/>candidates by mass<br/>(all adduct forms)"]
    A --> C["MS2 Enhancer<br/><b>candidates by fragmentation</b><br/>(matched vs ISDB)"]
    B --> D["Weights Enhancer<br/>scoring &amp; reweighting"]
    C --> D
    D --> E["Results / RDF export"]
    style C fill:#1e3a8a,stroke:#93c5fd,color:#ffffff,stroke-width:2px
```

---

## 2. The reference library: ISDB

The [**ISDB**](https://oolonek.github.io/ISDB/) is a library of MS/MS spectra that were
**predicted computationally** for known natural-product structures (rather than measured on
standards). Each library entry carries:

- a **precursor m/z** and the **adduct form** it was predicted for (typically `[M+H]⁺` in
  positive mode, `[M-H]⁻` in negative mode);
- the predicted **fragment peaks** (m/z + intensities);
- a **short InChIKey** identifying the structure.

In this pipeline the library lives in a DuckDB table (`library_spectra`), and only the spectra
whose precursor falls near a feature are read from it (Stage 1, §3). Each library spectrum
names its structure by InChIKey, and a match is **looked up in LOTUS** by the 14-character
short InChIKey. What the resulting annotation carries depends on whether LOTUS knows the
structure:

```mermaid
%%{init: {'theme':'dark'}}%%
flowchart LR
    LIB["library spectrum<br/>(precursor m/z, fragment peaks,<br/>InChIKey, InChI, SMILES, name)"] --> Q{"short InChIKey<br/>in LOTUS?"}
    Q -->|yes| LOT["<b>LOTUS-backed</b><br/>NPC score vectors +<br/>source organisms"]
    Q -->|no| LONLY["<b>library-only</b><br/>the library's own<br/>structure metadata"]
    LOT --> RW["reweighting + graph"]
    LONLY --> KG["graph only"]
    style LOT fill:#14532d,stroke:#86efac,color:#ffffff,stroke-width:2px
    style LONLY fill:#1e3a8a,stroke:#93c5fd,color:#ffffff,stroke-width:2px
```

- **LOTUS-backed.** The annotation carries LOTUS's NPC classification vectors and every source
  organism LOTUS lists for the structure. It is the only kind that feeds the
  taxonomical/chemical reweighting.
- **Library-only.** The annotation carries what the library asserts about the structure:
  InChIKey, InChI, SMILES, formula, name, and its NPClassifier and ClassyFire labels as plain
  strings. It has no source organism, so the reweighting skips it (`WeightsEnhancer` filters on
  `has_organisms()`), but it reaches the knowledge graph with its structure attached. Its
  classification vectors are zero-filled at full vocabulary length, so every array product
  downstream still lines up.

A match produces **no annotation** when the library row names no structure, or when LOTUS does
not know the structure and its InChIKey is not well formed (`XXXXXXXXXXXXXX-XXXXXXXXXX-X`, 14,
10 and 1 uppercase letters). The run log reports both counts and lists the malformed values
found, so they can be traced back to the library export.

How much each kind matters depends on the library. For `FRAGHUB_POS_LC:2026.03`
(1,450,368 spectra):

| | spectra | distinct structures |
|---|---|---|
| short InChIKey in LOTUS | 501,393 (34.6%) | 9,777 |
| short InChIKey not in LOTUS | 948,975 (65.4%) | 194,515 |
| of which the InChIKey is malformed | 287 | 3 |

The 287 malformed values are SMILES strings sitting in the export's InChIKey field (for example
`CCCCCCCCCCCCCC`). They are 14 uppercase letters, like the first block of a real InChIKey, so only
the full key's shape tells them apart.

---

## 3. The heart of it: two-stage matching

Comparing every experimental spectrum against every library spectrum with a full
fragmentation comparison would be enormously expensive. So the MS2 enhancer uses a **funnel**:
a cheap test first removes almost all pairs, and the expensive test runs only on the few that
survive.

```mermaid
%%{init: {'theme':'dark'}}%%
flowchart TD
    ALL["All (feature × library) pairs<br/>— astronomically many"] --> S1{"<b>Stage 1 — PrecursorMzMatch</b><br/>do the precursor m/z agree<br/>within parent_mz_tol?"}
    S1 -->|no| DROP["discarded (cheaply)"]
    S1 -->|yes| SURV["surviving pairs<br/>(same precursor mass)"]
    SURV --> S2{"<b>Stage 2 — MS/MS cosine</b><br/>CosineGreedy / CosineHungarian:<br/>score ≥ min_score AND<br/>matched peaks ≥ min_peaks?"}
    S2 -->|no| DROP2["discarded"]
    S2 -->|yes| HIT["accepted match →<br/>MS2ChemicalAnnotation"]
    style HIT fill:#14532d,stroke:#86efac,color:#ffffff,stroke-width:2px
    style S1 fill:#78350f,stroke:#fcd34d,color:#ffffff,stroke-width:2px
    style S2 fill:#1e3a8a,stroke:#93c5fd,color:#ffffff,stroke-width:2px
```

**Stage 1 — precursor m/z agreement (cheap).** The `PrecursorMzMatch` filter keeps only the
(feature, library-entry) pairs whose **precursor masses agree** within the parent tolerance
(`parent_mz_tol`, default 0.01 Da). No fragments are compared yet — this is just a fast
gate that throws away the overwhelming majority of pairs. It runs in batch over a chunk of
features against the whole library at once.

**Stage 2 — fragmentation cosine (expensive).** For each surviving pair, the enhancer computes
the actual **MS/MS cosine similarity** (`CosineGreedy` or `CosineHungarian`), which aligns the
two peak lists within the fragment tolerance (`msms_mz_tol`) and measures how much their
fragmentation patterns overlap. A pair is accepted as a match only if it clears **both**
thresholds:

- cosine score above `min_score` (default 0.20), **and**
- number of matched peaks above `min_peaks` (default 6).

Features are processed in **chunks** (default 1000) against the full library so memory stays
bounded regardless of dataset size.

---

## 4. Adduct gating — only annotate base-ion features

Before the funnel runs, the enhancer decides **which features are even worth matching**, using
the cluster roles the [MS1 graph enhancer](MS1_GRAPH_ENHANCER.md) stamped on each feature.

The reason is the same one §6 spells out: the ISDB stores **base ions** (`[M+H]⁺` in positive
mode, `[M-H]⁻` in negative). A feature the graph resolved as a *non-base* adduct — a
**satellite** like `[M+Na]⁺`/`[M+K]⁺` — has a precursor m/z that will never line up with a
base-ion library entry, so it could only ever be discarded at Stage 1 (or, rarely, produce a
spurious cross-adduct match). Its molecule's base ion is already represented by the cluster's
**anchor**. Skipping satellites up front saves that wasted Stage-1 work and removes the
coincidental-match risk.

Which features pass the gate is controlled by `ms2_adduct_filter` (on the shared
`MSEnhancerConfig`, read only by MS2):

| `ms2_adduct_filter` | annotates | skips | rationale |
|---|---|---|---|
| `"non_satellite"` **(default)** | anchors + singletons | resolved satellites | drop the redundant `[M+Na]⁺`/`[M+K]⁺` re-detections; keep every base-ion candidate |
| `"base_only"` | anchors only | satellites + singletons | strictest — only features *resolved* as the base ion |
| `"all"` | every feature | nothing | legacy behaviour (no gating) |

A **singleton** is a feature with no detected adduct relationships; its ionization form is
unknown, but it may well be an `[M+H]⁺` whose Na/K siblings fell below detection. The default
therefore keeps singletons (they are a large fraction of a run) and skips only features
*positively* resolved as another adduct. `"base_only"` is available when you want to match
strictly the confirmed base ions.

This gate depends on the `ms1_graph` block running **before** `ms2` (it does, by registry
order). If no cluster roles are present — the graph block was not selected — gating is
impossible, so **every feature is annotated** and a warning is logged; MS2 behaves exactly as
it did before this feature existed.

---

## 5. What comes out

Every accepted match becomes an **`MS2ChemicalAnnotation`** appended to the feature's
`ms2_annotations` list. A library often holds several spectra of one compound (collision
energies, instruments, or the same spectrum supplied by two source collections, as GNPS
redistributes MSnLib), so one feature can match one structure many times: on the fixture
dataset, 1,569 LOTUS-backed matches named only 113 distinct structure–feature pairs. The
feature therefore keeps **one annotation per library and structure** (2D InChIKey), from its
best-scoring spectrum: highest cosine, then most matched peaks, then the lowest library
spectrum id. That is also the granularity of the graph, which keys an MS2 annotation node on
feature, library and 2D InChIKey, and it makes the reweighting count each structure once. The
run log reports how many scored matches were merged this way. Each annotation carries:

- the matched structure's **short InChIKey**,
- the **cosine score** and **number of matched peaks**,
- the structure's **NPC classification arrays** (pathway / superclass / class) — LOTUS's
  scores, or zeros for a library-only match,
- the list of **source organisms** (for taxonomic reweighting later) — empty for a
  library-only match,
- `source` — `"Lotus"`, or the library's `name:version` label when the structure detail came
  from the library, and
- exactly one of `lotus` (a reference to the matched LOTUS entry, which the `LotusStore`
  already holds for the whole run) or `library_structure` (the library's own record).

```mermaid
%%{init: {'theme':'dark'}}%%
classDiagram
    class AnnotatedSpectrum {
      +float precursor_mz
      +list ms2_annotations
    }
    class MS2ChemicalAnnotation {
      +str source
      +str short_inchikey
      +float score
      +int n_matched_peaks
      +str queried_against
      +ndarray pathway_scores
      +ndarray superclass_scores
      +ndarray class_scores
    }
    class AnnotationOrganism {
      +str name
      +str species
      +str genus
      +str family
    }
    class Lotus {
      +str structure_inchikey
      +str structure_smiles
      +str structure_name_traditional
    }
    class LibraryStructure {
      +str inchikey
      +str inchi
      +str smiles
      +str molecular_formula
      +str compound_name
      +str npc_pathway
      +str classyfire_class
    }
    AnnotatedSpectrum "1" --> "0..*" MS2ChemicalAnnotation : ms2_annotations
    MS2ChemicalAnnotation "1" --> "0..*" AnnotationOrganism : organisms
    MS2ChemicalAnnotation "1" --> "0..1" Lotus : lotus
    MS2ChemicalAnnotation "1" --> "0..1" LibraryStructure : library_structure
```

In the knowledge graph, each annotation points at the shared 2D-InChIKey node
(`emi:hasChemicalStructure`), and the full structure node — built from `lotus` or from
`library_structure` — hangs off that same node via `emi:hasInChIKey2D`. Either way the
structure's SMILES, InChI and name are one hop from the annotation.

As with MS1, these are **competing, scored-but-not-yet-ranked** hypotheses. A single feature
may collect several MS2 annotations.

---

## 6. MS1 vs MS2 — the channel boundary

This is the most important conceptual point, and a common source of confusion. The two
enhancers are **independent channels** that never share their candidate machinery:

| | **MS1 enhancer** | **MS2 enhancer** |
|---|---|---|
| Evidence used | precursor **mass only** | precursor mass **+ fragmentation** |
| Matching key | inverts **all adduct recipes** to neutral mass | compares the **stored library precursor m/z** directly |
| Adduct coverage | broad — `[M+H]⁺`, `[M+Na]⁺`, `[M+NH₄]⁺`, multimers, … | restricted to whatever form **ISDB stored** (usually `[M+H]⁺`/`[M-H]⁻`) |
| Confidence | low (many isobaric candidates) | higher (fragmentation must agree) |
| Output slot | `spectrum.ms1_annotations` | `spectrum.ms2_annotations` |

A practical consequence worth stating in a talk: because Stage 1 compares the **observed
precursor m/z** against the **library's stored precursor m/z**, MS2 will only annotate a
feature when it was observed in the **same adduct form the ISDB provides**. A feature seen as
`[M+Na]⁺` will *not* match an ISDB `[M+H]⁺` entry — their precursor m/z differ by ~21.98 Da,
far beyond the tolerance. This is by design, not a defect: the **MS1 channel** is what covers
the broader adduct space, while **MS2** adds fragmentation confidence within the library's
adduct form. The two are complementary.

```mermaid
%%{init: {'theme':'dark'}}%%
flowchart LR
    F["One feature<br/>(observed m/z + MS/MS)"] --> M1["MS1: which neutral masses,<br/>under ANY adduct, fit this m/z?"]
    F --> M2["MS2: does the MS/MS match a<br/>library entry at this SAME m/z?"]
    M1 --> O["complementary evidence,<br/>kept separate, merged at ranking time"]
    M2 --> O
    style O fill:#14532d,stroke:#86efac,color:#ffffff,stroke-width:2px
```

> **Caveat — confident MS2 matches prune MS1 at export (RDF coupling).** In the RDF serializer
> the two channels are *coupled* on features that carry a **confident** MS2 match: one whose
> spectral score reaches `ms2_coupling_min_score`, a setting in the serializer section of the
> run setup (default 0.7). On such a feature, only the MS1 adducts that explain how the matched
> compound ionised are kept, each linked from the MS2 annotation via
> `enpkg:hasCorrespondingAdduct`; the mass-coincidence adducts are dropped. Which adducts
> explain the ionisation depends on what the match names:
>
> - a structure **LOTUS knows**: the adducts whose candidate structures include it (shared 2D
>   InChIKey);
> - a **library-only** structure: the adducts whose molecular formula equals the library
>   structure's. LOTUS holds at most isomers of it, so those adducts list the isomers as
>   candidates, not the structure itself.
>
> A consequence to expect: **if a confident match corresponds to none of the feature's MS1
> adducts, the feature is serialized with no MS1 adduct hypotheses at all**. This is
> **intended**: MS1 here is mass-only corroboration for the fragmentation-confirmed identity, so
> MS1 hypotheses that contradict it carry no information worth emitting. MS2 matches **below**
> the threshold are still emitted, but they neither remove nor keep MS1 adducts and get no
> link; a feature whose matches are all below it keeps its top-k MS1 adducts, as does a feature
> with no MS2 match. (See [MS2_MS1_ADDUCT_COUPLING_PLAN.md](MS2_MS1_ADDUCT_COUPLING_PLAN.md).)
>
> **Choosing the threshold.** The spectral match itself only requires `min_score` 0.2: a low
> entry gate inherited from matching predicted ISDB spectra before taxonomic re-ranking
> ([Rutz et al. 2019](https://doi.org/10.3389/fpls.2019.01329)), not a confidence level. The
> 0.7 default follows the GNPS library-search convention. Across 70 public datasets, most
> reached 1% FDR at cosine 0.6–0.65, and the cosine needed falls as more peaks must match
> ([Scheubert et al. 2017](https://doi.org/10.1038/s41467-017-01318-5)); a fixed threshold
> gives a different FDR in every dataset. Setting it to 0 makes every emitted match decide.
> On the fixture dataset (660 features, 2,633 MS1 hypotheses without coupling), coupling
> removed 282 MS1 hypotheses with no threshold, and 210, 190, 172 and 147 at 0.5, 0.6, 0.7
> and 0.8.

---

## 7. Why it's built this way

- **Adduct gating first.** Spectral libraries are base-ion only, so matching resolved non-base
  adducts is wasted work; using the MS1 graph's roles to skip them shrinks the candidate set
  before the funnel even starts, with a safe fallback when the graph did not run.
- **Two-stage funnel.** Fragmentation cosine is the dominant cost; gating on precursor mass
  first means it runs on a tiny fraction of pairs instead of the full cross-product.
- **Chunking.** Processing features in fixed-size chunks against the whole library keeps peak
  memory bounded, so the same code scales from a toy dataset to a full campaign.
- **Lazy library + LOTUS linking.** The expensive build (materialising the LOTUS list and
  attaching it to library spectra by short InChIKey) is deferred to the first `enhance()`
  call, so in batch mode only the first experiment pays for it; later experiments reuse it.
- **Slim annotations.** Each `MS2ChemicalAnnotation` keeps the InChIKey, score, NPC arrays and
  organisms, plus either a *reference* to the matched LOTUS entry (already held for the run, so
  it costs one pointer) or the library's small structure record — so thousands of them stay
  light.

---

### One-line summary

> **The MS2 enhancer confirms identities by fragmentation: a cheap precursor-mass gate
> followed by an MS/MS cosine comparison against the ISDB library, yielding scored candidate
> annotations — higher-confidence than MS1, but limited to the adduct form the library
> stores.**
