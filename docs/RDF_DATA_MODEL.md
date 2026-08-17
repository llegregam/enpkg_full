# RDF Data Model — entities to map against the EMI vocabulary

> **Status: superseded worksheet.** This was the blank mapping worksheet (Vocab
> term = `TODO`). The **filled-in, current** mapping lives in
> [RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md) and the implementation in
> [enpkg/monolith/rdf/serializer.py](../enpkg/monolith/rdf/serializer.py). Kept
> for history; consult the mapped version for anything authoritative.

Inventory of every node the `AnalysisSerializer` will emit, with each field
pre-sorted into its RDF shape. **Identity is already handled** by the named
`uris.py` minter in each heading — this worksheet is for filling the **Vocab
term** column (the `rdf:type` class, and the predicate for each link/literal)
against EMI (Earth Metabolome Ontology) and the vocabularies it reuses
(SOSA, NPC, PROV-O, plus `owl`/`rdfs`/`dcterms`).

## How to use
For each row, pick the matching term from `EMI-vocab.owl` (or SOSA/NPC/PROV-O)
and replace `TODO`. Mark `MINT` where nothing fits and a custom `emi:` term is
needed. Shapes: **type** = `rdf:type` object · **obj** = object property
(points at another node URI) · **lit** = datatype property (literal value).

## Scope flags
- ⛔ **skip** — out of the first cut.
- ⏸ **gated** — behind an `include_*` flag (graph bloat).

## Namespaces (from `rdf/namespaces.py`)
`EMI` `https://w3id.org/emi#` · `EMI_RES` `…/emi/resource/` · `WD` Wikidata ·
`INCHIKEY` identifiers.org · `SOSA` · `MS` PSI-MS. EMI imports SOSA, NPC, PROV-O.

## Identity (done — `rdf/uris.py`)
| Entity | Minter | Key |
|---|---|---|
| Analysis | `AnalysisURIs.analysis_uri` | `analysis/{run_name}` |
| SampleMetadata | `analysis_metadata_uri` | `metadata/{run_name}` |
| AnnotatedSpectrum | `spectrum_uri` | `spectrum/{run}/{feature_id}` |
| ChemicalAdduct | `chemical_adduct_uri` | `adduct/{run}/{feature}/{recipe_hash}` |
| AdductRecipe | `recipe_uri` | `recipe/{recipe_hash}` (shared) |
| MS2ChemicalAnnotation | `ms2_annotation_uri` | `ms2ann/{run}/{feature}/{lib}/{short_inchikey}` |
| Lotus (compound) | `CompoundURIs.lotus_uri` | InChIKey → WD → smiles-hash (shared) |
| AnnotationOrganism / source organism | `OrganismURIs.organism_uri` / `source_organism_uri` | WD → OTT (shared) |
| Match | `ott_match_uri` | `ottmatch/{run}/{ott_id}` |
| Network edge | — (blank node) | — |

---

## 1. Analysis — `analysis_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| hasSample | obj | → Sample | TODO |
| hasSpectrum (×N) | obj | → Spectrum | TODO |
| hasOTTMatch (×N) | obj | → OTTMatch | TODO |
| run_name | lit | xsd:string | TODO |
| ionization_mode | lit | xsd:string | TODO |

## 2. SampleMetadata — `analysis_metadata_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| sourceOrganism | obj | → Organism | TODO |
| sample_id | lit | xsd:string | TODO |
| source_taxon | lit | xsd:string | TODO |
| sample_type | lit | xsd:string | TODO |
| source_id | lit | xsd:string | TODO |
| organism_kingdom / phylum / class / order / family / genus | lit | xsd:string | TODO |
| massive_id | obj/lit | owl:sameAs MassIVE? | TODO |
| sample_filename_pos / neg | lit | xsd:string | TODO |
| extra_fields | — | ⛔ skip | — |

## 3. AnnotatedSpectrum — `spectrum_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — (mass spectrum / sosa:Observation) | TODO |
| hasMS1Adduct (×N) | obj | → ChemicalAdduct | TODO |
| hasMS2Annotation (×N) | obj | → MS2Annotation | TODO |
| feature_id | lit | xsd:integer | TODO |
| precursor_mz | lit | xsd:double | TODO |
| retention_time | lit | xsd:double | TODO |
| intensity | lit | xsd:double | TODO |
| polarity (derived) | lit | xsd:boolean | TODO |
| charge | lit | xsd:integer | TODO |
| mz / intensities (peaks) | lit | ⏸ gated | TODO |
| ms1_/ms2_ pathway/superclass/class_scores | — | ⛔ skip (NPC vectors) | — |

## 4. ChemicalAdduct (MS1) — `chemical_adduct_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| hasRecipe | obj | → AdductRecipe | TODO |
| annotatesCompound (×N) | obj | → Compound (`lotus_uri`) | TODO |
| adduct_mass | lit | xsd:double | TODO |
| molecular_formula (derived) | lit | xsd:string | TODO |
| short_inchikey (derived) | lit | xsd:string | TODO |
| positive (derived) | lit | xsd:boolean | TODO |
| get_*_scores | — | ⛔ skip (NPC vectors) | — |

## 5. AdductRecipe — `recipe_uri` (shared)
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| hasIngredient (×N) | obj | → blank node `[name, count]` | TODO |
| → ingredientName | lit | xsd:string | TODO |
| → ingredientCount | lit | xsd:double | TODO |
| charge | lit | xsd:double | TODO |
| positive | lit | xsd:boolean | TODO |
| multimer_factor | lit | xsd:double | TODO |

## 6. MS2ChemicalAnnotation — `ms2_annotation_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| annotatesCompound | obj | → structure @ **short-InChIKey** (⚠ decision A) | TODO |
| producedBy (×N) | obj | → Organism (`organism_uri`) | TODO |
| source | lit | xsd:string | TODO |
| score | lit | xsd:double | TODO |
| n_matched_peaks | lit | xsd:integer | TODO |
| queried_against | lit | xsd:string | TODO |
| pathway/superclass/class_scores | — | ⛔ skip (NPC vectors) | — |

## 7. AnnotationOrganism — `organism_uri` (shared)
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| owl:sameAs | obj | → WD (`wikidata`) / OTT (`ott_id`) | owl:sameAs |
| name | lit | xsd:string | TODO |
| domain / kingdom / phylum / klass / order / family / genus / species | lit | xsd:string | TODO |

## 8. Lotus (compound) — `lotus_uri` (shared)
> A LOTUS row is a **structure–organism–reference triplet** → emits 3 nodes.

### 8a. Compound (structure)
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| owl:sameAs | obj | → PUBCHEM (`structure_cid`) / WD (`structure_wikidata`) | owl:sameAs |
| producedBy | obj | → Organism (§8b) | TODO |
| documentedBy | obj | → Reference (§8c) | TODO |
| structure_inchikey | lit | xsd:string | TODO |
| structure_inchi | lit | xsd:string | TODO |
| structure_smiles | lit | xsd:string | TODO |
| structure_molecular_formula | lit | xsd:string | TODO |
| structure_exact_mass | lit | xsd:double | TODO |
| structure_xlogp | lit | xsd:double | TODO |
| structure_name_iupac / _traditional | lit | xsd:string | TODO |
| classyfire 01kingdom / 02superclass / 03class / 04directparent / chemontid | lit/obj | xsd:string / sameAs | TODO |
| structure_stereocenters_total / _unspecified | lit | xsd:integer | TODO |
| manual_validation | lit | xsd:boolean | TODO |
| structure_taxonomy_hammer_* | — | ⛔ skip (NPC vectors) | — |
| structure_smiles_2d | lit | ⛔ skip? | — |

### 8b. Organism (compound's source) — reuse `organism_uri` cascade
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| owl:sameAs | obj | → WD (`organism_wikidata`) / GBIF (`gbifid`) / NCBI (`ncbiid`) / OTT (`ottid`) | owl:sameAs |
| organism_name | lit | xsd:string | TODO |
| domain / kingdom / phylum / klass / order / family / tribe / genus / species / varietas | lit | xsd:string | TODO |

### 8c. Reference
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| reference_doi | obj | → DOI node / dcterms:source | TODO |
| reference_wikidata | obj | owl:sameAs WD | owl:sameAs |

## 9. Match / Taxon — `ott_match_uri`
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| matchedTaxon / owl:sameAs | obj | → Organism / WD (`taxon.wikidata.wd`) | TODO |
| matched_name | lit | xsd:string | TODO |
| score | lit | xsd:double | TODO |
| is_approximate_match | lit | xsd:boolean | TODO |
| is_synonym | lit | xsd:boolean | TODO |
| nomenclature_code | lit | xsd:string | TODO |
| search_string | lit | xsd:string | TODO |
| taxon.open_tree_taxon_id | lit | xsd:integer | TODO |
| taxon.rank | lit | xsd:string | TODO |
| taxon.unique_name / name | lit | xsd:string | TODO |
| lineage domain…species (derived) | lit | xsd:string | TODO |
| flags / synonyms / tax_sources / is_suppressed* | — | ⛔ skip (initial) | — |

## 10. Molecular-network edge — blank node (no minter)
| Element | Shape | Target / type | Vocab term |
|---|---|---|---|
| rdf:type | type | — | TODO |
| connects | obj | → two Spectrum URIs (`u`, `v`) | TODO |
| edge `data` attrs (cosine score, …) | lit | xsd:double | TODO |

---

## Cross-cutting decisions to settle before serializing
- **A — split Compound identity.** MS1 adducts carry full `Lotus` → full-InChIKey
  Compound nodes (§8a). MS2 annotations carry only `short_inchikey` (§6) →
  skeleton level. Decide: separate short-InChIKey node, re-look-up the full
  structure, or link both at skeleton level.
- **B — Organism predicate names.** Organism nodes appear as sample source (§2),
  compound provenance (§8b), and MS2 provenance (§6) — all deduped by the one
  `_organism_uri` cascade. Keep the linking predicate consistent
  (`sourceOrganism` vs `producedBy`) or deliberately distinguish them.
- **C — AdductRecipe ingredients.** Blank-node `[name, count]` pairs (queryable)
  vs a flat literal string (compact). §5 assumes blank nodes.
