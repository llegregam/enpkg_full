# Optimize `import_spectral_db` — chunked Polars DataFrame insertion

## Context

`import_spectral_db` in `database_manager.py:402-476` crashes on large pickle files (~898 MB) due to memory issues and is very slow. The current approach:
1. Loads entire pickle into memory (~900 MB)
2. Builds a Python `list[tuple]` of ALL rows (doubles memory with `.tolist()` conversions)
3. Inserts everything at once with `executemany()` (slowest DuckDB insertion path)

Peak memory reaches 3-5 GB. Additionally, the `short_inchikey` column in `spectral_library` is being populated with `compound_name` — it was never correct. The user confirmed it should be **removed from the table entirely**.

## Plan

### Step 1: Remove `short_inchikey` from `spectral_library` schema

**File:** `enpkg/monolith/loaders/database_manager.py`

In the `_DDL` string (lines 85-102):
- Remove `short_inchikey TEXT NOT NULL,` from the `spectral_library` CREATE TABLE
- Remove the `idx_spectral_short_inchikey` index

### Step 2: Add `import gc` and chunk size constant

**File:** `enpkg/monolith/loaders/database_manager.py`

- Add `import gc` to imports (around line 17)
- Add `_SPECTRAL_IMPORT_CHUNK_SIZE = 50_000` after `_DDL` (around line 104)

### Step 3: Rewrite `import_spectral_db` with chunked Polars insertion

**File:** `enpkg/monolith/loaders/database_manager.py` (replace lines 402-476)

Key changes:
1. **Pickle load** stays the same (unavoidable with pickle format)
2. **Drop `idx_spectral_precursor_mz` index** before bulk insert (follows existing pattern at line 338)
3. **Chunked loop** (`range(0, n_total, chunk_size)`):
   - For each chunk, build column lists (not tuples) and construct a **Polars DataFrame**
   - Insert via `INSERT OR IGNORE INTO spectral_library (col, ...) SELECT col, ... FROM df` — uses DuckDB's native zero-copy Arrow path
   - **One transaction per chunk** — allows partial progress, keeps WAL small
   - **Null out processed spectra** (`spectra[idx] = None`) + `gc.collect()` to free memory progressively
4. **Recreate index** after all chunks inserted
5. **Sequential ID counter** (`next_id`) that only increments on valid spectra (no gaps)
6. Column list: `id, precursor_mz, mzs, intensities, mode, compound_name, adduct, charge, metadata_json` (no `short_inchikey`)

Memory profile improvement: peak drops from ~3-5 GB to ~1.0-1.1 GB (pickle + one chunk's DataFrame).

### Step 4: Update schema docs

**File:** `enpkg/monolith/loaders/DB_Project_build_schema.md` (line 19)

Remove reference to "Linked to compounds by short_inchikey" for spectral_library.

### Step 5: Add test for `import_spectral_db`

**File:** `enpkg/tests/test_enhancers/test_database_manager.py` (new)

Test that:
- Creates a few synthetic `matchms.Spectrum` objects (with known mz, intensities, compound_name, precursor_mz)
- Pickles them to a temp file
- Imports with a small `chunk_size=2` to exercise chunking boundaries
- Verifies row count and data correctness in DuckDB
- Verifies spectra with `precursor_mz=None` are skipped
- Uses `tmp_path` fixture for the DuckDB file and pickle file

## Files to modify

| File | Action |
|------|--------|
| `enpkg/monolith/loaders/database_manager.py` | Schema change + rewrite `import_spectral_db` |
| `enpkg/monolith/loaders/DB_Project_build_schema.md` | Remove spectral short_inchikey reference |
| `enpkg/tests/test_enhancers/test_database_manager.py` | New test file |

## Verification

1. Run the new test: `pytest enpkg/tests/test_enhancers/test_database_manager.py -v`
2. Run full test suite to check for regressions: `pytest enpkg/tests/ -v`
