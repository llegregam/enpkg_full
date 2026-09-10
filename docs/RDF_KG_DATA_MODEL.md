# RDF Knowledge-Graph Data Model

> **Role: the as-built model** — what the serializer emits *today*. Every box and edge below is
> live in [serializer.py](../enpkg/monolith/rdf/serializer.py). Its companion,
> [`_static/MAIN_SCHEMA.mmd`](_static/MAIN_SCHEMA.mmd), is the **target model** — where the schema
> is going, including terms not yet emitted. Neither supersedes the other; they answer different
> questions ("what will I find in my export?" vs. "what is this heading toward?").
>
> For what any `enpkg:` term *means* — its domain, range, and the reasoning behind minting it —
> the authority is [`vocab/enpkg.ttl`](vocab/enpkg.ttl), not this diagram. Its `[live]` / `[target]`
> tags encode exactly the split between these two documents. See
> [VOCABULARY.md](VOCABULARY.md) for the walkthrough.

The node types and relationships emitted by the `AnalysisSerializer`
([enpkg/monolith/rdf/serializer.py](../enpkg/monolith/rdf/serializer.py)). Boxes are
`rdf:type` classes; edge labels are the predicates that connect them; a few key literal
(datatype) properties are listed inside each box. External identifier authorities and
optional ("gated") layers are included for completeness.

```mermaid
%%{init: {'theme':'dark'}}%%
flowchart TD
    %% ---- node definitions ----
    A["LCMSAnalysis  (Pos / Neg)<br/>dcterms:identifier · emi:hasMassiveDOI"]
    SA["ExtractSample<br/>identifier · enpkg:sourceId · sampleFilenamePos/Neg · label=taxon"]
    FS["LCMSFeatureSet"]
    F["LCMSFeature  (a spectrum)<br/>hasRowId · hasParentMass · hasRetentionTime · hasFeatureArea<br/>enpkg:clusterRole · enpkg:resolvedAdduct '[M+Na]+' (if clustered)"]

    MS1["AdductAnnotation — MS1<br/>enpkg:adductMass (ion) · enpkg:adductNeutralMass · emi:hasAdduct '[M+H]+'<br/>annotationRank / annotationScore · +PSI-MS adduct-ion class"]:::ms1
    MS2["SpectralAnnotation — MS2<br/>emi:hasSpectralScore (cosine) · enpkg:nMatchedPeaks<br/>dcterms:source · annotationRank / annotationScore"]:::ms2
    SIR["SiriusAnnotation — SIRIUS<br/>chemrof:generalized_empirical_formula · emi:hasAdduct<br/>enpkg:annotationRank (structurePerIdRank, 1=best)"]:::sirius

    RC["AdductRecipe<br/>enpkg:charge · isPositive · multimerFactor"]
    IG["Ingredient<br/>ingredientName · ingredientCount"]
    CLU["AdductCluster — MS1 graph resolution<br/>enpkg:clusterConnectivity · clusterIntensityCoverage · clusterCountCoverage"]:::cluster

    CMP["ChemicalStructure  (compound)<br/>chemrof:inchi_key_string (full) · SMILES · formula<br/>monoisotopic_mass · xlogp · classyfire* · prefLabel/altLabel"]
    IK2D["InChIKey2D<br/>emi:inChIKey2D — 14-char skeleton"]
    REF["BibliographicResource<br/>dcterms:identifier — DOI"]

    TX["Taxon  (organism)<br/>emi:scientificName · identifier=OTT id · has_rank"]
    OTT["OTT Match  (Taxon)<br/>enpkg:matchScore · isApproximateMatch · isSynonym · searchString"]

    EXTC(["owl:sameAs targets<br/>Wikidata · PubChem"]):::ext
    EXTT(["owl:sameAs targets<br/>Wikidata · NCBITaxon · GBIF"]):::ext

    ION["Ion  — gated: include_ions<br/>enpkg:productIonMz · enpkg:productIonIntensity"]:::gated
    LF["LFpair<br/>emi:hasCosine · emi:hasMassDifference"]
    FBMN["FBMNComponent  — gated: include_fbmn_components (on)<br/>enpkg:componentSize"]:::cluster

    %% ---- spine ----
    SA -->|"enpkg:hasLabProcess"| A
    A -->|"emi:hasLCMSFeatureSet"| FS
    A -->|"enpkg:hasOTTMatch"| OTT
    FS -->|"emi:hasLCMSFeature"| F

    %% ---- annotations off each feature ----
    F -->|"emi:hasAnnotation"| MS1
    F -->|"emi:hasAnnotation"| MS2
    F -->|"emi:hasAnnotation"| SIR
    F -->|"enpkg:hasIon (gated)"| ION

    %% ---- MS1 adduct internals ----
    MS1 -->|"enpkg:hasRecipe"| RC
    RC -->|"enpkg:hasIngredient"| IG
    MS1 -->|"enpkg:hasCandidateStructure (one per isobaric isomer)"| CMP

    %% ---- MS1 adduct clusters (graph resolution) ----
    FS -->|"enpkg:hasAdductCluster"| CLU
    CLU -->|"enpkg:hasAnchor"| F
    CLU -->|"enpkg:hasClusterMember"| F

    %% ---- MS2 ↔ MS1 coupling (only on MS2-identified features) ----
    MS2 -->|"enpkg:hasCorrespondingAdduct"| MS1

    %% ---- MS2 & SIRIUS attach at the 2D level directly ----
    MS2 -->|"emi:hasChemicalStructure"| IK2D
    SIR -->|"emi:hasChemicalStructure"| IK2D

    %% ---- chemistry bridge ----
    CMP -->|"emi:hasInChIKey2D"| IK2D
    CMP -->|"prov:wasDerivedFrom"| REF

    %% ---- taxonomy (shared Taxon nodes) ----
    SA -->|"sosa:isSampleOf"| TX
    CMP -->|"emi:inTaxon"| TX
    MS2 -->|"emi:inTaxon"| TX

    %% ---- external identifiers ----
    CMP -->|"owl:sameAs"| EXTC
    TX  -->|"owl:sameAs"| EXTT
    OTT -->|"owl:sameAs"| EXTT

    %% ---- molecular network ----
    LF -->|"emi:hasFirstMember"| F
    LF -->|"emi:hasSecondMember"| F
    FS -->|"enpkg:hasNetworkComponent"| FBMN
    FBMN -->|"enpkg:hasComponentMember"| F
    F -->|"emi:hasFBMNComponent"| FBMN

    classDef ms1    fill:#78350f,stroke:#fcd34d,color:#ffffff,stroke-width:2px;
    classDef ms2    fill:#14532d,stroke:#86efac,color:#ffffff,stroke-width:2px;
    classDef sirius fill:#164e63,stroke:#67e8f9,color:#ffffff,stroke-width:2px;
    classDef gated  fill:#1f2937,stroke:#9ca3af,color:#e5e7eb,stroke-width:1px;
    classDef ext    fill:#3730a3,stroke:#a5b4fc,color:#ffffff,stroke-width:1px;
    classDef cluster fill:#4c1d95,stroke:#c4b5fd,color:#ffffff,stroke-width:2px;
```

## Legend & key points

- **Spine:** `ExtractSample → LCMSAnalysis` (the sample/metadata, via `enpkg:hasLabProcess` —
  `emi:hasSample` doesn't actually exist in EMI) and `LCMSAnalysis → LCMSFeatureSet →
  LCMSFeature`. One `LCMSFeature` per spectrum.
- **Adduct clusters (MS1 graph):** each resolved molecule is one `enpkg:AdductCluster`
  (`enpkg:hasAdductCluster` off the `LCMSFeatureSet`), carrying the mzAdan indices
  (`clusterConnectivity`/`clusterIntensityCoverage`/`clusterCountCoverage`). It names its base ion
  via `enpkg:hasAnchor` and every member via `enpkg:hasClusterMember`; each member feature carries
  `enpkg:clusterRole` (`anchor`/`satellite`) and its resolved form as a literal
  `enpkg:resolvedAdduct "[M+Na]+"` (not a parallel edge — the form is already on the feature's
  `emi:hasAnnotation` hypotheses). Singletons (no adduct relationships) get no cluster.
- **Network components (FBMN):** each connected component of the molecular network is one
  `emi:FBMNComponent` — a putative *structural family*, the network counterpart of the
  adduct cluster above (the two are declared `skos:closeMatch`). Keyed on its smallest member
  feature id, carrying `enpkg:componentSize`. Note the deliberate **predicate asymmetry**: the
  feature set anchors components with a *minted* `enpkg:hasNetworkComponent`, while each
  member feature points back with EMI's own `emi:hasFBMNComponent` — EMI declares the latter's
  `rdfs:domain` as `emi:LCMSFeature`, so using it off the feature set would entail that the
  feature set is a feature. Isolated features (no edges) get no component. Beware the naming
  asymmetry with the clusters: `enpkg:componentSize` here vs `enpkg:clusterConnectivity` there.
- **Three annotation channels** hang off every feature, all through the same `emi:hasAnnotation`
  predicate (SIRIUS used to attach via its own minted `enpkg:hasSiriusAnnotation`; unified since
  all three already subclass `emi:StructuralAnnotation` — a consumer tells them apart by
  `rdf:type`, not by predicate):
  - **MS1 — `AdductAnnotation`** (amber): mass-based adduct hypotheses. Links to an
    `AdductRecipe` (which decomposes into `Ingredient`s) and to **full-InChIKey** `ChemicalStructure`
    compounds — **one edge per isobaric isomer** in the formula group. Note the predicate:
    MS1 uses **`enpkg:hasCandidateStructure`**, *not* the `emi:hasChemicalStructure` that MS2 and
    SIRIUS use. Deliberately a sibling and not a subproperty — an MS1 hit is a mass coincidence,
    not a confirmed identification, and keeping the two unlinked is what lets a consumer query
    "candidate" and "confirmed" apart. It carries both masses of the hypothesis:
    `enpkg:adductMass` (the observed ion) and `enpkg:adductNeutralMass` (the candidate's own
    neutral mass, from which the first is derived via the recipe).
  - **MS2 — `SpectralAnnotation`** (green): fragmentation matches. Attaches **directly** to the
    `InChIKey2D` node, because MS2 only resolves the 2D skeleton (short InChIKey).
  - **SIRIUS — `SiriusAnnotation`** (cyan): in-silico structure identifications. Carries the
    predicted `generalized_empirical_formula` and `emi:hasAdduct`, and attaches **directly** to the
    `InChIKey2D` node (2D skeleton). Candidates arrive pre-ranked by SIRIUS's `structurePerIdRank`
    (1=best), stamped as `enpkg:annotationRank` — no NPC reweighting (unlike MS1/MS2).
  - All three subclass `emi:StructuralAnnotation`, so a consumer can query them uniformly or split
    them by the `enpkg:AdductAnnotation` / `SpectralAnnotation` / `SiriusAnnotation` subclass.
- **MS2 ↔ MS1 coupling (`enpkg:hasCorrespondingAdduct`):** on a feature with an MS2 match, the two
  channels are joined — only the MS1 adducts whose candidate structures include the MS2-identified
  compound (shared 2D short InChIKey) are emitted, and each MS2 annotation links to its corresponding
  adduct(s). Every matching adduct *form* is kept (the MS1 `top_k` cap is bypassed); the
  mass-coincidence adducts are dropped. A feature whose MS2 compound is in no MS1 group ends up with
  no MS1 adduct at all (intended). Features with **no** MS2 keep their full top-k MS1 hypotheses.
- **`InChIKey2D` is the bridge** between the channels: MS1 reaches it via the compound's
  `emi:hasInChIKey2D`; MS2 and SIRIUS point at it directly. This is the node to join on when asking
  "did MS1, MS2 and SIRIUS agree on the same structure?".
- **Adduct identity:** an `AdductAnnotation` IRI is
  `emi-res:adduct/{run}/{feature}/{recipe hash}/{formula}` — one node per *(candidate formula
  group, adduct recipe)* on a feature, because that pair is exactly what a `ChemicalAdduct` is.
  Two molecules proposed for the same feature under the same ionization form are two nodes, each
  with its own rank, masses and candidate structures. The formula is a readable segment rather
  than a second hash so a hypothesis can be identified by eye in the Turtle.
- **Compound identity:** the `ChemicalStructure` IRI is `identifiers.org/inchikey/<full key>`
  (the full 27-char InChIKey); the full key is also a literal `chemrof:inchi_key_string`.
- **Taxonomy:** `Taxon` nodes are shared (deduplicated) across the sample source
  (`sosa:isSampleOf`), each compound's source organism (`emi:inTaxon`), and the MS2 match's
  source organisms (`emi:inTaxon`). The `OTT Match` node carries match-quality literals and is
  also typed `emi:Taxon`.
- **External authorities** are reached with `owl:sameAs` (Wikidata, PubChem, NCBITaxon, GBIF;
  references additionally link to DOIs).
- **Gated layers:** product `Ion` nodes (`include_ions`, off) are grey. The molecular-network
  `LFpair` edges are emitted whenever the networking block ran. The `FBMNComponent` nodes
  derived from those edges are gated by `include_fbmn_components` and default **on** — they
  are O(features) where the edges are O(features²), and the component is the unit consumers
  query. All are no-ops when the upstream enhancer did not run.
- **Ranking note:** `annotationRank` / `annotationScore` are stamped **per channel** — MS1
  adducts and MS2 matches are each ranked by the reweighted NPC-alignment score among themselves;
  SIRIUS keeps its own `structurePerIdRank` as `annotationRank`. There is no merged cross-channel rank.
