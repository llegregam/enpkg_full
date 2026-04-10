# Database Architecture Plan

## 1. Schema Design

### Core Tables (Reference Data)

**`compounds`** - LOTUS metadata
- structure_smiles (PRIMARY KEY)
- inchikey, short_inchikey
- molecular_formula, exact_mass
- taxonomy fields (organism_*, etc.)

**`npc_classifications`** - NPC pathway/superclass/class probability distributions
- Linked to compounds by SMILES
- Stores probability vectors for pathway, superclass, class

**`spectral_library`** - Reference spectra
- precursor_mz, peaks (as binary/array)
- Linked to compounds by short_inchikey
- Indexed by precursor_mz for fast range queries

### Analysis Tables (Results)

**`analyses`** - One row per sample
- sample_id, metadata, OTT matches, timestamps

**`spectra`** - Experimental spectra per analysis
- feature_id, precursor_mz, peaks, retention_time
- Foreign key to analyses

**`ms1_annotations`** - MS1 adduct matches
- Links spectra → compounds
- adduct_type, mass_error, score

**`ms2_annotations`** - ISDB spectral matches
- cosine_score, n_matches
- Links spectra → spectral_library entries

**`reweighted_scores`** - Final taxonomic/chemical weighted scores
- msms_score, taxo_score, chemo_score, final_score


## 2. DatabaseManager Class

A singleton/context manager that:
- Handles connection pooling to DuckDB file
- Provides methods:
  - `get_compounds_by_formula(formula) -> list[Compound]`
  - `get_spectra_by_mass_range(low, high) -> list[Spectrum]`
  - `save_analysis(analysis) -> analysis_id`
  - `load_analysis(analysis_id) -> Analysis`
- Lazy-loads reference data (LOTUS/spectral DB) on first query
- Supports batch inserts for performance


## 3. Migration Strategy

### Phase 1 - Import existing CSV/pkl data
- One-time script to ingest current LOTUS CSVs and spectral pickles into DuckDB tables
- Much faster subsequent loads (~100x for the previous 27-second class loading)

### Phase 2 - Modify loaders
- `DBLoader` becomes a thin wrapper around `DatabaseManager`
- Replace pandas DataFrames with DuckDB queries that return only needed rows
- Keep pandas for in-memory manipulation when needed (DuckDB integrates natively)


## 4. Analysis Serialization

- After each enhancer runs, persist results to the analysis tables
- `Analysis` object gets `save()` and `load()` methods
- Enables resumable pipelines and provenance tracking


## 5. RDF Export (Turtle)

- Query the analysis tables and emit triples
- Map domain model to ontology:
  - ChEBI for compounds
  - Custom ontology for annotations and analyses
- Use rdflib or direct string templating for .ttl generation


## Benefits Comparison

| Current Approach              | DuckDB Approach                        |
|-------------------------------|----------------------------------------|
| 40s to load CSVs to pandas    | <1s query from indexed tables          |
| In-memory only, lost on crash | Persistent, queryable history          |
| No provenance                 | Full audit trail of all analyses       |
| Manual RDF generation         | Direct SQL → RDF export                |


## Recommended Implementation Order

1. **Start with the schema** - Draft the DDL for DuckDB
2. **Build the import script** - One-time migration of existing data
3. **Create DatabaseManager** - Central access point with connection management
4. **Incrementally update enhancers** - One at a time, keeping tests passing
5. **Add RDF export** - After analysis persistence is working
