# RDF Data Model — EMI-prefilled mapping (auto-draft)

Derived by extracting `enpkg/monolith/rdf/EMI-vocab.owl` (6,408 triples; 134
classes, 113 object props, 54 datatype props) and matching our entities to EMI
terms. **Base worksheet `RDF_DATA_MODEL.md` is untouched** — decide later whether
to keep this or redo from the base.

Prefixes: `emi:` = `https://w3id.org/emi#` (EMI-native). Imports referenced by
their canonical prefix: `sosa:` `prov:` `dcterms:` `owl:` `skos:` `bibo:`.
Confidence: ✅ direct EMI term · 🟡 plausible/needs check · ⛏ **MINT** (no EMI term).

---

## EMI restructurings this mapping implies (read first)
EMI's shape differs from our object tree in five important ways:

1. **Analysis → FeatureSet → Feature.** EMI inserts an intermediate node:
   `emi:LCMSAnalysis ──emi:hasLCMSFeatureSet──▶ emi:LCMSFeatureSet
   ──emi:hasLCMSFeature──▶ emi:LCMSFeature`. Our spectra are `emi:LCMSFeature`
   ("LCMS individual MS2 spectrum"), reached via a feature-set node, **not** a
   direct `hasSpectrum`.
2. **MS1 and MS2 annotations unify** under `emi:StructuralAnnotation`
   (`emi:LCMSFeature ──emi:hasAnnotation──▶ emi:StructuralAnnotation`). The
   adduct is a **string** on it (`emi:hasAdduct`), not a node — so `recipe_uri`
   isn't needed for EMI alignment (kept only if you want queryable recipes).
3. **Decision A is resolved by EMI.** Structures carry both
   `emi:InChIKey` (full) and `emi:InChIKey2D` (the 2D / short InChIKey), linked
   `emi:hasInChIKey2D`. So MS2's `short_inchikey` → an `emi:InChIKey2D` node;
   MS1's full InChIKey → an `emi:InChIKey` node; the two are linked. No
   skeleton-vs-full ambiguity.
4. **NPC scores have a real home — not "skip".** `emi:ChemicalTaxonAnnotation`
   carries `emi:hasPathwayProbability` / `hasSuperClassProbability` /
   `hasClassProbability` and links `emi:hasPathway`/`hasSuperClass`/`hasClass`
   → `emi:Pathway`/`Superclass`/`Class`. You *can* serialize the NPC vectors
   here later (still gate them for size).
5. **Molecular network = `emi:LFpair`** (`emi:hasFirstMember`/`hasSecondMember`
   → `LCMSFeature`), with clusters as `emi:FBMNComponent` (`emi:hasFBMNComponent`).
   Note `emi:hasCosine`/`hasMassDifference` are declared on `SpectralPair`
   (MS2-spectrum pairs), so cosine-on-LFpair may need a check or a SpectralPair.

Biggest gaps (⛏ MINT — no external term): OTT-match quality (score/approximate/
synonym), `n_matched_peaks`, `xlogp`, stereocenters. External vocabs cover the
rest: PSI-MS (`rdf/ms-voab.owl`) → adduct family / `charge` / ionization mode;
ChEBI-ChemROF (`rdf/chebi-vocab.owl`) → formula / mass / smiles / inchi /
inchikey; NCBITaxon slim (`rdf/NCBITaxon_slim-vocab.owl`) → organism lineage +
rank — see §4/§5/§8a/§8b.

---

## 1. Analysis — `analysis_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:LCMSAnalysis` (+ `emi:LCMSAnalysisPos`/`Neg` by ionization_mode) |
| hasSample | obj | → Sample | ✅ `emi:hasSample` |
| (feature set) | obj | → FeatureSet | ✅ `emi:hasLCMSFeatureSet` |
| hasSpectrum (×N) | obj | FeatureSet → Spectrum | ✅ `emi:hasLCMSFeature` (off the FeatureSet node) |
| hasOTTMatch (×N) | obj | → OTTMatch node (kept — §9) | ⛏ custom `enpkg:hasOTTMatch` |
| run_name | lit | xsd:string | ✅ `dcterms:identifier` |
| ionization_mode | lit | — | ✅ `MS:1000009` ("ionization mode"); or encode via `emi:LCMSAnalysisPos`/`Neg` subclass |
| massive_id | lit | — | ✅ `emi:hasMassiveDOI` (EMI puts it on the Analysis, not the Sample) |

## 2. SampleMetadata — `analysis_metadata_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:ExtractSample` |
| sourceOrganism | obj | → Organism | ✅ `sosa:isSampleOf` |
| sample_id | lit | xsd:string | ✅ `dcterms:identifier` |
| source_taxon | lit | xsd:string | ✅ `rdfs:label` (declared name; resolved organism via `sosa:isSampleOf`) |
| sample_type | type/lit | — | ✅ EMI sample subclass by value (`emi:QCSample`/`Blank`/`ExtractSample`) |
| source_id | lit | xsd:string | ⛏ custom `enpkg:sourceId` |
| organism_kingdom…genus | lit | — | → put on the Organism node (§7), not the Sample |
| sample_filename_pos/neg | lit | xsd:string | ⛏ custom `enpkg:sampleFilenamePos`/`Neg` |
| extra_fields | — | ⛔ skip | — |

## 3. AnnotatedSpectrum — `spectrum_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:LCMSFeature` |
| hasMS1Adduct / hasMS2Annotation | obj | → StructuralAnnotation | ✅ `emi:hasAnnotation` (both kinds) |
| feature_id | lit | xsd:integer | ✅ `emi:hasRowId` |
| precursor_mz | lit | xsd:double | ✅ `emi:hasParentMass` |
| retention_time | lit | xsd:double | ✅ `emi:hasRetentionTime` |
| intensity | lit | xsd:double | ✅ `emi:hasFeatureArea` / `emi:hasRelativeFeatureArea` |
| polarity (derived) | lit | — | → from Analysis Pos/Neg; drop on feature |
| charge | lit | xsd:integer | ✅ `MS:1000041` ("charge state") |
| mz / intensities (peaks) | lit | ⏸ gated | 🟡 `emi:hasRawSpectrum` (string) |
| consensus spectrum | obj | → GNPSConsensusSpectrum | ✅ `emi:hasConsensusSpectrum` (if GNPS) |
| FBMN component | obj | → FBMNComponent | ✅ `emi:hasFBMNComponent` |

## 3a. Product ion (MS2 peak) — blank node · ⏸ gated + intensity-filtered
> Each peak in `spectrum.mz`/`intensities` → one ion node via `enpkg:hasIon`.
> Blank nodes (a peak is owned by one spectrum, never shared/referenced; use
> named `ion/{run}/{feature}/{idx}` URIs only if you must point at a specific
> fragment). **HEAVY**: ~100 peaks × ~4 triples ≈ 400 triples/spectrum.
>
> **Bloat control — serialization-time projection (never mutates the spectrum):**
> - `include_ions: bool = False` — the gate (off by default).
> - `min_relative_intensity: float = 0.0` — keep peaks ≥ fraction of the base
>   peak (`intensities >= t * intensities.max()`); robust across spectra/instruments.
> - `max_ions_per_spectrum: int | None = None` — top-N hard cap on triples/spectrum.
>
> Reuse matchms: `select_by_relative_intensity` then `reduce_to_number_of_peaks`.
> The emitted peak list is then a **lossy subset**, so stamp the applied cutoff on
> the spectrum (below) — otherwise consumers read it as the complete peak list.

**Spectrum-level provenance (on the LCMSFeature, only when filtered):**
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| low intensity threshold | lit | xsd:double (% base peak) | ✅ `MS:1000629` ("low intensity threshold"; unit `MS:1000132` "percent of base peak") |
| (top-N cap, if used) | lit | xsd:integer | ⛏ custom `enpkg:maxIonsPerSpectrum` (optional, for reproducibility) |

**Ion node:**
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| (spectrum → ion) | obj | LCMSFeature → ion (×N) | ⛏ custom `enpkg:hasIon` |
| rdf:type | type | — | ⛔ none — `MS:1000342` ("product ion") is **obsolete** with no non-obsolete replacement; the node is identified by `enpkg:hasIon` + the m/z & intensity value props |
| m/z | lit | xsd:double | ✅ `MS:1001225` ("product ion m/z") — cvParam, see §4 note |
| intensity | lit | xsd:double | ✅ `MS:1001226` ("product ion intensity") — cvParam, see §4 note |

## 4. ChemicalAdduct (MS1) — `chemical_adduct_uri`

> **Identity:** `adduct/{run}/{feature}/{recipe_hash}/{formula}`. A `ChemicalAdduct` is a
> *(LOTUS formula group, recipe)* pairing, so both halves are in the key. Keying on the
> recipe alone (the pre-2026-08-10 scheme) merged every molecule proposed for one feature
> under one ionization form into a single node — see `docs/MS1_ADDUCT_RANKING_ISSUE.md` §2.

| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:StructuralAnnotation` + `enpkg:AdductAnnotation` (MS1 subclass); polarity-specific `MS:1002807` (pos) / `MS:1002808` (neg). Parent `MS:1000353` ("adduct ion") dropped as redundant — it is entailed by the polarity class |
| adduct form | lit | "[M+H]+" string | ✅ `MS:1002813` ("adduct ion formula"; regex `MS:1002812`) — alongside `emi:hasAdduct` |
| hasRecipe | obj | → AdductRecipe node | custom `enpkg:hasRecipe` (structured composition; §5) |
| annotatesCompound (×N) | obj | → ChemicalStructure | custom `enpkg:hasCandidateStructure` — a *sibling* of `emi:hasChemicalStructure`, deliberately not a subproperty: an MS1 hit is a mass coincidence, not a confirmed identification (MS2/SIRIUS use the EMI term) |
| adduct_mass | lit | xsd:double | ✅ `MS:1003243` ("adduct ion mass"); `MS:1003635` if monoisotopic — see cvParam note |
| neutral_mass | lit | xsd:double | custom `enpkg:adductNeutralMass` (`skos:closeMatch chemrof:monoisotopic_mass`) — the candidate group's own mass, from which `adduct_mass` is derived via the recipe |
| molecular_formula (derived) | lit | → on the structure; also the last segment of the adduct IRI | (see §8a) |
| short_inchikey (derived) | lit | → InChIKey2D node | (see §8a) |
| positive (derived) | type | — | ✅ via `MS:1002807`/`MS:1002808` rdf:type |
| scores | lit | xsd:double | ✅ `emi:hasFinalScore` / `hasTaxoScore` / `hasConsistencyScore` (Sirius-style) |

> **cvParam note**: PSI-MS terms (`MS:1003243`, …) are OBO controlled-vocabulary
> *classes* meant for value typing, not OWL predicates. Either reify the value
> (`[ a MS:1003243 ; rdf:value 19.018 ]`) or use a plain datatype property linked
> to the term (`… skos:exactMatch MS:1003243`), as EMI does with its own `emi:has*`
> props. `MS:1002807-8` used as `rdf:type` are unambiguous.

## 5. AdductRecipe — `recipe_uri`
> **Decision: use a structured recipe node** (custom). PSI-MS gives the adduct
> *formula string* (`MS:1002813`) and the abstract *adduct* product
> (`MS:1003055`), but **no structured ingredient list** — so the composition is
> minted under your own `enpkg:` namespace.
>
> `recipe_uri` and `_recipe_hash` were deliberately **not** touched when the adduct
> identity was fixed (§4): the recipe node is globally shared across every adduct that
> applies it, so widening its hash would have moved every recipe IRI for no gain.

| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | custom `enpkg:AdductRecipe` (cf. `MS:1003055` "adduct") |
| hasIngredient (×N) | obj | → blank `[name, count]` | custom `enpkg:hasIngredient` |
| ingredientName / ingredientCount | lit | string / double | custom `enpkg:ingredientName` / `enpkg:ingredientCount` |
| charge | lit | xsd:integer | ✅ `MS:1000041` ("charge state") |
| multimer_factor | lit | xsd:double | custom `enpkg:multimerFactor` |
| (formula string) | lit | "[M+H]+" | ✅ `MS:1002813` ("adduct ion formula") |

## 6. MS2ChemicalAnnotation — `ms2_annotation_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:StructuralAnnotation` + `enpkg:SpectralAnnotation` (MS2 subclass) |
| annotatesCompound | obj | → ChemicalStructure (→ `emi:InChIKey2D` for the short key) | ✅ `emi:hasChemicalStructure` |
| producedBy (×N) | obj | → Organism | ✅ `emi:inTaxon` ("found in taxon") |
| source | obj | → source DB resource | ✅ `prov:wasDerivedFrom` (e.g. a LOTUS dataset node) |
| score | lit | xsd:double | ✅ `emi:hasSpectralScore` |
| n_matched_peaks | lit | xsd:integer | ⛏ custom `enpkg:nMatchedPeaks` |
| queried_against | lit/obj | library | ✅ `dcterms:source` (the spectral library, e.g. ISDB) |
| pathway/superclass/class_scores | obj | → ChemicalTaxonAnnotation | ✅ via `emi:ChemicalTaxonAnnotation` (§ NPC) — gate for size |
| (corresponding MS1 adduct) | obj | → `enpkg:AdductAnnotation` (§4) | ⛏ custom `enpkg:hasCorrespondingAdduct` — the MS1 adduct proposing the same compound (shared 2D InChIKey); emitted only on MS2-identified features, which also prunes the non-corresponding MS1 adducts. See D2. |

## 7. AnnotationOrganism — `organism_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:Organism` / `emi:Taxon` |
| owl:sameAs | obj | → WD / OTT | ✅ `owl:sameAs` |
| name | lit | xsd:string | ✅ `emi:scientificName` |
| rank | obj | → Rank | ✅ `emi:rank` → `emi:Rank` (or `ncbitaxon:has_rank` if NCBI-linked) |
| domain…species | lit/obj | lineage | 🟡 flat literals — `AnnotationOrganism` has no NCBI id; add an `ncbi_id` field to link `obo:NCBITaxon_{id}` + its hierarchy |

## 8. Lotus → 3 nodes (Compound / Organism / Reference) — `lotus_uri`
### 8a. Compound (structure)
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:ChemicalStructure` |
| full InChIKey | obj | → InChIKey node | ✅ `emi:hasInChIKey` → `emi:InChIKey` (value `emi:inChIKey`) |
| short InChIKey | obj | → InChIKey2D node | ✅ `emi:hasInChIKey2D` → `emi:InChIKey2D` (value `emi:inChIKey2D`) |
| structure_smiles | lit | xsd:string | ✅ `emi:hasSMILES` (or `chemrof:smiles_string`) |
| structure_inchi | lit | xsd:string | ✅ `chemrof:inchi_string` (ChEBI/ChemROF) |
| owl:sameAs | obj | → PUBCHEM(cid) / WD | ✅ `owl:sameAs` (WD: `emi:WDChemicalStructure`) |
| producedBy | obj | → Organism | ✅ `emi:inTaxon` |
| documentedBy | obj | → Reference | ✅ `prov:wasDerivedFrom` |
| molecular_formula | lit | xsd:string | ✅ `chemrof:generalized_empirical_formula` (ChEBI/ChemROF) |
| structure_exact_mass | lit | xsd:double | ✅ `chemrof:monoisotopic_mass` (ChEBI/ChemROF; our exact_mass is monoisotopic) |
| structure_xlogp | lit | xsd:double | ⛏ custom `enpkg:xlogp` |
| name_traditional / name_iupac | lit | xsd:string | ✅ `skos:prefLabel` / `skos:altLabel` |
| classyfire 01..04 / chemontid | obj/lit | — | ✅ `owl:sameAs` chemontid → ChemOnt IRI; 4 ranks as literals (defer node modeling) |
| stereocenters_total/_unspecified | lit | xsd:integer | ⛏ custom `enpkg:stereocentersTotal`/`Unspecified` |
| manual_validation | lit | xsd:boolean | ⛏ custom `enpkg:manualValidation` |
| hammer_* (NPC) | obj | → ChemicalTaxonAnnotation | ✅ see § NPC (gated) |

> **ChemROF note**: `chemrof:*` (from ChEBI v252, `rdf/chebi-vocab.owl`) are
> `owl:AnnotationProperty` — use them **directly as predicates** (no reification,
> unlike PSI-MS cvParam classes). Full set: `charge`, `mass`, `monoisotopic_mass`,
> `generalized_empirical_formula`, `smiles_string`, `inchi_string`,
> `inchi_key_string`, `wurcs_representation`. ChEBI also offers `owl:sameAs` to
> `CHEBI:` class IRIs if you resolve structures to ChEBI IDs.

### 8b. Organism (compound source) — reuse `organism_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| owl:sameAs | obj | → WD/GBIF/NCBI/OTT | ✅ `owl:sameAs` (NCBI: `obo:NCBITaxon_{organism_taxonomy_ncbiid}`) |
| organism_name | lit | xsd:string | ✅ `emi:scientificName` |
| rank | obj | → rank | ✅ `ncbitaxon:has_rank` → `obo:NCBITaxon_{rank}` (or `emi:rank`) |
| domain…varietas | lit/obj | lineage | ✅ link `emi:inTaxon`/`owl:sameAs` → `obo:NCBITaxon_{ncbiid}`; hierarchy + rank come from NCBITaxon `rdfs:subClassOf` (flat literals then optional) |

### 8c. Reference
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:BibliographicResource` |
| reference_doi | (node IRI) | `https://doi.org/{doi}` | ✅ node IRI = the DOI URL; + `dcterms:bibliographicCitation` |
| reference_wikidata | obj | owl:sameAs WD | ✅ `owl:sameAs` |

## NPC classification (Compound / annotation → chemical taxonomy)
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:ChemicalTaxonAnnotation` |
| pathway prob / link | lit/obj | double / → Pathway | ✅ `emi:hasPathwayProbability` · `emi:hasPathway` → `emi:Pathway` |
| superclass prob / link | lit/obj | double / → Superclass | ✅ `emi:hasSuperClassProbability` · `emi:hasSuperClass` → `emi:Superclass` |
| class prob / link | lit/obj | double / → Class | ✅ `emi:hasClassProbability` · `emi:hasClass` → `emi:Class` |

## 9. Match / Taxon — `ott_match_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:Taxon` (match node kept; quality fields below) |
| owl:sameAs | obj | → WD (`taxon.wikidata.wd`) | ✅ `owl:sameAs` |
| matched_name / taxon.name | lit | xsd:string | ✅ `emi:scientificName` |
| taxon.rank | obj | → Rank | ✅ `emi:rank` → `emi:Rank` |
| taxon.open_tree_taxon_id | lit | xsd:integer | ✅ `dcterms:identifier` |
| score / is_approximate_match / is_synonym | lit | — | ⛏ custom `enpkg:matchScore` / `isApproximateMatch` / `isSynonym` |
| nomenclature_code / search_string | lit | xsd:string | ⛏ custom `enpkg:nomenclatureCode` / `searchString` |
| lineage domain…species | lit/obj | — | 🟡 literals / Taxon hierarchy |

## 10. Molecular-network edge — blank node
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | ✅ `emi:LFpair` (feature pair) · clusters → `emi:FBMNComponent` |
| connects | obj | → two LCMSFeature | ✅ `emi:hasFirstMember` / `emi:hasSecondMember` |
| cosine score | lit | xsd:double | ✅ `emi:hasCosine` — declared on `SpectralPair`, and `LFpair rdfs:subClassOf SpectralPair`, so the domain is satisfied and no `emi:SpectralPair` workaround is needed |
| mass difference | lit | xsd:double | ✅ `emi:hasMassDifference` — same reasoning; emitted as the absolute precursor-mass difference |

---

## Custom `enpkg:` vocabulary to define (everything else resolves to EMI / PSI-MS / ChEBI / NCBITaxon)
- **Recipe** (§5): `AdductRecipe`, `hasRecipe`, `hasIngredient`, `ingredientName`, `ingredientCount`, `multimerFactor`
- **Ion / peak** (§3a): `hasIon`, `maxIonsPerSpectrum`
- **OTT match** (§1/§9): `hasOTTMatch`, `matchScore`, `isApproximateMatch`, `isSynonym`, `nomenclatureCode`, `searchString`
- **MS2** (§6): `nMatchedPeaks`
- **Annotation classes** (§4/§6): `AdductAnnotation` (MS1) and `SpectralAnnotation` (MS2), both `rdfs:subClassOf emi:StructuralAnnotation` — differentiate the two annotation kinds EMI otherwise unifies.
- **Annotation ranking** (§4/§6): `annotationRank`, `annotationScore` (reweighted top-k, derived from the weights-enhancer propagated NPC scores; MS2 falls back to cosine when NPC scores are absent). Serialization caps each spectrum at **top-k = 5** per side by default (`top_k_ms1`/`top_k_ms2`; pass `<=0` to emit all).
- **Numeric value props** (§3a/§4/§5) — each `owl:DatatypeProperty` + `skos:exactMatch` to its PSI-MS *class* (PSI-MS has no properties, so using the class as a predicate would pun it): `adductMass` (→MS:1003243), `charge` (→MS:1000041), `productIonMz` (→MS:1001225), `productIonIntensity` (→MS:1001226), `lowIntensityThreshold` (→MS:1000629). The adduct **formula** string uses the real EMI property `emi:hasAdduct` (`"[M+H]+"`). PSI-MS terms now appear only as `rdf:type` on adduct nodes (`MS:1002807`/`1002808`); the obsolete product-ion type (`MS:1000342`) and the redundant parent `MS:1000353` have been dropped.
- **Adduct clusters (MS1 graph resolution)**: `AdductCluster` (`owl:Class`, `skos:closeMatch emi:FBMNComponent`); `hasAdductCluster` (FeatureSet→Cluster), `hasClusterMember`, `hasAnchor`, `inAdductCluster` (Feature→Cluster); `clusterConnectivity` / `clusterIntensityCoverage` / `clusterCountCoverage` (CGC/CIC/CCC); `clusterRole` and `resolvedAdduct` (literals on the feature — the resolved form as a `"[M+Na]+"` string, not a node edge). One cluster node per resolved molecule; singletons produce none. Implemented — see [DATA_MODEL_AND_SERIALIZATION_GAP.md](DATA_MODEL_AND_SERIALIZATION_GAP.md) §D1.
- **Compound scalars** (§8a): `xlogp`, `stereocentersTotal`, `stereocentersUnspecified`, `manualValidation`
- **Sample** (§2): `sourceId`, `sampleFilenamePos`, `sampleFilenameNeg`
- **Resolved by PSI-MS (`ms-voab.owl`)**: adduct polarity class (`MS:1002807`/`1002808`; the parent `MS:1000353` is entailed, not emitted), adduct formula (`MS:1002813`), `adduct_mass` (`MS:1003243`/`1003635`), `charge` (`MS:1000041`), ionization mode (`MS:1000009`)
- **Resolved by ChEBI/ChemROF (`chebi-vocab.owl`)**: `molecular_formula`, `exact_mass`, SMILES, InChI, InChIKey, formal charge — via `chemrof:` annotation properties (§8a)
- **Resolved by NCBITaxon slim (`NCBITaxon_slim-vocab.owl`)**: organism lineage + rank → `obo:NCBITaxon_{ncbiid}` node (hierarchy via `rdfs:subClassOf`, rank via `ncbitaxon:has_rank`); applies where an NCBI id exists (Lotus organism — not yet `AnnotationOrganism`)

## Open items unchanged from base
- **B — organism predicate (resolved)**: `sosa:isSampleOf` for sample→organism; `emi:inTaxon` for compound/annotation→taxon.
- **C — recipe**: resolved — adduct *form* uses `MS:1002813` (formula string) + `emi:hasAdduct`; structured composition uses a custom `enpkg:AdductRecipe` node (your decision).
