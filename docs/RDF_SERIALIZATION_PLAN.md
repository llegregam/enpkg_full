# RDF Serialization for `Analysis` Objects

> **Status: historical planning document (largely implemented).** The serializer
> described here now lives in [enpkg/monolith/rdf/](../enpkg/monolith/rdf/)
> (`AnalysisSerializer`, `serialize_to_turtle`) and is covered by
> `enpkg/tests/test_data/test_serializer.py`. The concrete vocabulary mapping is
> maintained in [RDF_DATA_MODEL_mapped.md](RDF_DATA_MODEL_mapped.md); this file
> is kept for the original design rationale. Where the two disagree, the code and
> `RDF_DATA_MODEL_mapped.md` win.

## Context

The user wants to convert the in-memory `Analysis` object hierarchy into an RDF graph
to build a knowledge graph. Starting from scratch — no prior schema in this codebase
will be reused. The `Analysis` object aggregates everything produced for a single
mass-spectrometry run: sample metadata, a list of annotated spectra (each with MS1
adduct hypotheses and MS2 spectral-library matches), OpenTree taxonomy matches for
the sample's source organism, and the molecular network linking spectra.

Design decisions confirmed with the user:
- Conversion lives in a dedicated module (data classes stay pure)
- Custom ENPKG vocabulary, with external URIs (Wikidata, OTT, InChIKey) used as
  objects where stable IDs exist
- Output is an `rdflib.Graph` with a thin Turtle export helper
- URIs prefer external identifiers; fall back to project-minted URIs derived from
  stable composite keys (run_name + feature_id, etc.)

`rdflib 7.5.0` is already a project dependency — no new deps required.

## Module Layout

New package: [enpkg/monolith/rdf/](enpkg/monolith/rdf/)

- [enpkg/monolith/rdf/__init__.py](enpkg/monolith/rdf/__init__.py) — re-exports the
  public API (`AnalysisSerializer`, `serialize_to_turtle`)
- [enpkg/monolith/rdf/namespaces.py](enpkg/monolith/rdf/namespaces.py) —
  `rdflib.Namespace` definitions + custom ENPKG vocabulary terms (classes and
  predicates)
- [enpkg/monolith/rdf/uris.py](enpkg/monolith/rdf/uris.py) — pure functions that
  mint a `URIRef` for each entity type, encapsulating the
  "external-ID-where-possible, project-URI-otherwise" rule
- [enpkg/monolith/rdf/serializer.py](enpkg/monolith/rdf/serializer.py) —
  `AnalysisSerializer` class that walks an `Analysis` and emits triples into an
  `rdflib.Graph`

## Namespaces

In `namespaces.py`:

```python
ENPKG      = Namespace("https://enpkg.example.org/vocab#")        # vocabulary
ENPKG_RES  = Namespace("https://enpkg.example.org/resource/")     # project resources
WD         = Namespace("http://www.wikidata.org/entity/")
OTT        = Namespace("https://tree.opentreeoflife.org/taxonomy/browse?id=")
INCHIKEY   = Namespace("https://identifiers.org/inchikey/")
PUBCHEM    = Namespace("https://identifiers.org/pubchem.compound/")
GBIF       = Namespace("https://www.gbif.org/species/")
NCBITAXON  = Namespace("http://purl.obolibrary.org/obo/NCBITaxon_")
DOI        = Namespace("https://doi.org/")
```

Custom classes (under `ENPKG`): `Analysis`, `Sample`, `Spectrum`, `MS1Adduct`,
`MS2Annotation`, `Compound`, `Organism`, `OTTMatch`, `MolecularNetworkEdge`.

Custom predicates: `hasSample`, `hasSpectrum`, `hasOTTMatch`, `hasMS1Adduct`,
`hasMS2Annotation`, `annotatesCompound`, `producedBy` (compound→organism),
`precursorMz`, `retentionTime`, `intensity`, `featureId`, `adductRecipe`,
`adductCharge`, `cosineSimilarity`, `linkedTo` (network edge), `ionizationMode`,
`sourceTaxon`, `taxonRank`, `inchikey`, `smiles`, `molecularFormula`, `exactMass`,
`xlogp`. (Final list grows as the serializer is written.)

## URI Minting Rules (`uris.py`)

| Entity | Primary URI | Fallback |
|---|---|---|
| `Analysis` | `ENPKG_RES["analysis/{run_name}"]` | — |
| `SampleMetadata` | `ENPKG_RES["sample/{sample_id}"]` | — |
| `AnnotatedSpectrum` | `ENPKG_RES["spectrum/{run_name}/{feature_id}"]` | — |
| `ChemicalAdduct` (MS1) | `ENPKG_RES["adduct/{run_name}/{feature_id}/{recipe_hash}"]` | — |
| `MS2ChemicalAnnotation` | `ENPKG_RES["ms2ann/{run_name}/{feature_id}/{idx}"]` | — |
| `Lotus` (compound) | `INCHIKEY[structure_inchikey]` | `WD[structure_wikidata]` → project UUID |
| `Lotus` (organism) | `WD[organism_wikidata]` | `OTT[organism_taxonomy_ottid]` → `GBIF[..]` → `NCBITAXON[..]` → project UUID |
| `Match` / `Taxon` | `OTT[open_tree_taxon_id]` | project UUID |
| Molecular-network edge | blank node | — |

`recipe_hash` is a short, stable hash of the `AdductRecipe` (charge + sorted
ingredient dict) so the same adduct hypothesis produces the same URI across runs.

## Triple-Emission Sketch (per entity)

`AnalysisSerializer.add_analysis(analysis)` walks the object and emits, roughly:

- **Analysis** — `rdf:type ENPKG:Analysis`, `ENPKG:runName`, `ENPKG:ionizationMode`,
  `ENPKG:hasSample → SampleNode`, `ENPKG:hasSpectrum → SpectrumNode` (×N),
  `ENPKG:hasOTTMatch → OTTMatchNode` (×N)
- **SampleMetadata** — `rdf:type ENPKG:Sample`, `ENPKG:sampleId`,
  `ENPKG:sourceTaxon`, `ENPKG:sampleType`, organism lineage fields. `extra_fields`
  emitted as `ENPKG:extraField` blank-node key/value pairs (or skipped initially —
  decide once we look at real data).
- **AnnotatedSpectrum** — `rdf:type ENPKG:Spectrum`, `ENPKG:featureId`,
  `ENPKG:precursorMz`, `ENPKG:retentionTime`, `ENPKG:intensity`, polarity,
  `ENPKG:hasMS1Adduct →` (×N), `ENPKG:hasMS2Annotation →` (×N). The peak arrays
  (`mz`/`intensities`) are skipped by default — they'd bloat the graph; expose an
  `include_peaks=False` flag on the serializer.
- **ChemicalAdduct** — `rdf:type ENPKG:MS1Adduct`, `ENPKG:adductRecipe` (string
  representation), `ENPKG:adductMass`, `ENPKG:adductCharge`,
  `ENPKG:annotatesCompound → CompoundNode` (×N, one per Lotus entry)
- **MS2ChemicalAnnotation** — `rdf:type ENPKG:MS2Annotation`, `ENPKG:source`,
  scores flattened as `ENPKG:cosineSimilarity` etc. (the `scores` dict is nested;
  the initial cut emits `dc:source` + top-level scalar scores only),
  `ENPKG:annotatesCompound → CompoundNode` (×N)
- **Lotus (compound)** — `rdf:type ENPKG:Compound`, `ENPKG:inchikey`, `ENPKG:smiles`,
  `ENPKG:molecularFormula`, `ENPKG:exactMass`, `ENPKG:xlogp`, `ENPKG:iupacName`,
  PubChem CID via `owl:sameAs PUBCHEM[cid]` if present, ClassyFire taxonomy as
  literal strings (kingdom/superclass/class/directparent), `ENPKG:producedBy →
  OrganismNode`, `dcterms:source DOI[reference_doi]` if present
- **Lotus (organism)** — `rdf:type ENPKG:Organism`, `ENPKG:organismName`, lineage
  string fields (kingdom..species), `owl:sameAs` linking to GBIF / NCBI Taxon / OTT
  alternate IDs
- **Match / Taxon** — `rdf:type ENPKG:OTTMatch`, `ENPKG:matchedName`, `ENPKG:score`,
  `ENPKG:taxonRank`, `ENPKG:isApproximateMatch`, `ENPKG:isSynonym`, lineage,
  `owl:sameAs WD[wikidata]` if `WikidataOTTQuery` is present
- **Molecular network** — for each edge `(u, v, data)`: a blank node typed
  `ENPKG:MolecularNetworkEdge` connecting the two spectrum URIs, with edge
  attributes (cosine score, etc.) as literal properties. Standalone nodes need
  no extra triple (already covered by `hasSpectrum`).

`AnnotatedSpectrum` subclasses `matchms.Spectrum` and is not a Pydantic model, so
the serializer reads its attributes (`feature_id`, `precursor_mz`, etc.) directly
rather than via `model_dump()`. Everything else is Pydantic and can use
`model_fields` introspection where it's convenient.

The serializer keeps a `set[URIRef]` of already-emitted compound/organism/taxon
URIs so a compound shared across many spectra is described only once.

## Public API

```python
from enpkg.monolith.rdf import AnalysisSerializer, serialize_to_turtle

serializer = AnalysisSerializer()
serializer.add_analysis(analysis)            # can be called multiple times
graph = serializer.graph                     # rdflib.Graph
turtle_str = graph.serialize(format="turtle")
serialize_to_turtle(analysis, "out.ttl")     # convenience one-shot
```

## Critical Files To Add

- [enpkg/monolith/rdf/__init__.py](enpkg/monolith/rdf/__init__.py)
- [enpkg/monolith/rdf/namespaces.py](enpkg/monolith/rdf/namespaces.py)
- [enpkg/monolith/rdf/uris.py](enpkg/monolith/rdf/uris.py)
- [enpkg/monolith/rdf/serializer.py](enpkg/monolith/rdf/serializer.py)

## Critical Files To Read While Implementing

(Existing — for field accessors and identifier semantics)
- [enpkg/monolith/data/analysis.py](enpkg/monolith/data/analysis.py)
- [enpkg/monolith/data/sample_metadata.py](enpkg/monolith/data/sample_metadata.py)
- [enpkg/monolith/data/annotated_spectra_class.py](enpkg/monolith/data/annotated_spectra_class.py)
- [enpkg/monolith/data/otl_class.py](enpkg/monolith/data/otl_class.py) — `Match`, `Taxon`, `WikidataOTTQuery`
- [enpkg/monolith/data/lotus_class.py](enpkg/monolith/data/lotus_class.py)
- [enpkg/monolith/data/chemical_annotation.py](enpkg/monolith/data/chemical_annotation.py)
- [enpkg/monolith/data/ms1_data_classes.py](enpkg/monolith/data/ms1_data_classes.py) — `ChemicalAdduct`, `AdductRecipe`

## Verification

1. **Unit test** — build a small fixture `Analysis` (one spectrum, one Lotus, one
   Match), run the serializer, assert: (a) the analysis URI has expected type and
   predicates, (b) a Lotus with an `inchikey` produces the right `INCHIKEY:` URI,
   (c) calling `add_analysis` twice on the same analysis does not duplicate
   compound nodes (deduplication works).
2. **Round-trip** — `g.serialize(format="turtle")` then parse back into a fresh
   graph; compare triple counts.
3. **Manual inspection** — serialize a real analysis to `out.ttl`, open it, eyeball
   that compound/organism nodes are linked sensibly and external URIs resolve.
4. **SPARQL sanity check** — run a small SPARQL query against the in-memory graph
   ("list all compounds linked to spectrum X") and confirm expected results.
5. **Optional load test** — `rdflib.Graph().parse("out.ttl")` should succeed
   without warnings; GraphDB / Blazegraph import optional next step.

## Scope Boundaries

Not in scope for this first cut:
- Peak arrays (mz/intensity vectors) — gated behind a flag, default off
- NPC/Hammer score vectors — large numpy arrays, defer to a later iteration
- `extra_fields` from `SampleMetadata` — emit a follow-up once we see real values
- SHACL validation, JSON-LD output, named graphs / quad export
- Bulk export of many analyses (the API supports it via repeated
  `add_analysis(...)`, but no CLI/runner is built here)
