# Building and using the reference database

*How to put LOTUS and one or more spectral libraries into a DuckDB file, and how to choose
between them at run time.*

> The pipeline **only ever reads** this database. Building it is an offline step you do
> once per machine, and repeat when a library is updated. Nothing in a pipeline run writes
> to it.

---

## 1. What the database holds

One `.duckdb` file, holding two independent things:

| Part | Built by | Read by |
|---|---|---|
| LOTUS compounds + NPC classifications | `enpkg db lotus` | MS1, MS2, weights (`lotus_store`) |
| Registered spectral libraries | `enpkg db spectral-library` | MS2 (`spectral_library_store`) |

Four tables result:

- `compounds`, `npc_classifications` — LOTUS.
- `spectral_library_registry` — one row per library: its name, version, ion mode, whether
  it is experimental or in-silico, and how many spectra it holds.
- `library_spectra` — every spectrum from every library, each row carrying the
  `library_id` of the library it came from.

**Spectra from all libraries share one table.** Choosing which libraries a run searches is
therefore a filter on an indexed column, not a matter of opening different files. That is
what makes "search the experimental library but not the in-silico one" a configuration
change rather than a data-management exercise.

---

## 2. The constraint that shapes everything below

**Spectral libraries reach this pipeline only through FragHub.**

[FragHub](https://github.com/eMetaboHUB/FragHub) harmonises MSP, MGF, JSON, CSV and XML
from any open library into one schema. Format diversity is handled there, and the importer
here accepts exactly one shape: a FragHub CSV export.

So there is no "ISDB importer" and no "GNPS importer". If you want ISDB in your database,
you run ISDB through FragHub first and import the CSV that comes out. The same is true of
any other library.

**FragHub exports in buckets, and one bucket is one library here.** A bucket is segregated
by ion mode, by separation technique, and by whether its spectra are experimental or
predicted. The importer enforces that: a CSV mixing two ion modes, or mixing experimental
and predicted spectra, is rejected with a message telling you to import each bucket
separately. It reads the mode and the predicted flag from the file's own `IONMODE` and
`PREDICTED` columns rather than from the filename, because FragHub's filenames are
inconsistently cased and do not reliably identify the bucket.

The practical consequence: **"FragHub + ISDB" is not two imports, it is one import per
bucket per library.** Positive and negative mode are always separate registrations.

---

## 3. Building LOTUS

You need four CSVs from the LOTUS distribution:

```bash
enpkg db lotus \
    --database gui_workspace/databases/enpkg.duckdb \
    --metadata     gui_workspace/databases/230106_frozen_metadata.csv \
    --pathways     gui_workspace/databases/pathways.csv \
    --superclasses gui_workspace/databases/superclasses.csv \
    --classes      gui_workspace/databases/classes.csv
```

The database file is created if it does not exist. Re-running on a populated database
reports its row counts and stops rather than rebuilding, so an accidental repeat cannot
quietly redo a multi-gigabyte import; pass `--force` when you mean it.

You can confirm it worked from the first line a run logs:

```
LotusStore ready (36 compound columns, 7 pathways, 77 superclasses, 696 classes)
```

---

## 4. Adding spectral libraries

### 4.1 Prepare the exports with FragHub

Run each library through FragHub and keep the per-bucket CSVs. For a positive-mode LC
experimental library plus ISDB (which is in-silico), you would end up with something like:

```
FragHub_out/
├── POS_LC_EXP/POS_LC.csv      experimental, positive, LC
├── NEG_LC_EXP/NEG_LC.csv      experimental, negative, LC
└── ISDB_POS_LC_INSILICO/…csv  predicted,    positive, LC
```

Each CSV must carry these columns, or the import fails:

| Column | Why it is required |
|---|---|
| `PRECURSORMZ`, `PEAKS_LIST` | the spectrum itself |
| `MSLEVEL`, `IONMODE` | decide whether and how it is stored |
| `INCHIKEY` | the join key to LOTUS — without it a match cannot reach a compound |
| `PRECURSORTYPE` | the ionisation form (the adduct) |

Anything else FragHub emits is kept: recognised fields such as `SMILES`, `FORMULA`,
`NAME`, `SPLASH` and the NPClassifier and ClassyFire columns get their own columns;
everything unrecognised is preserved in `metadata_json` and reported at import. That is
deliberate — a new FragHub release can add fields without breaking ingestion, and the
addition is still visible to you.

### 4.2 Check before you commit

`--dry-run` validates the header and resolves twenty rows without writing anything. It is
worth doing for every new export:

```bash
enpkg db spectral-library \
    --database gui_workspace/databases/enpkg.duckdb \
    --name FragHub_POS_LC_EXP --version 2024.1 \
    --path FragHub_out/POS_LC_EXP/POS_LC.csv \
    --separation LC \
    --dry-run
```

This is where a mixed bucket, a missing required column, or an unexpected new column shows
up — before you spend the time importing several hundred thousand spectra.

### 4.3 Import

Drop `--dry-run` to do it for real:

```bash
# Experimental, positive mode
enpkg db spectral-library \
    --database gui_workspace/databases/enpkg.duckdb \
    --name FragHub_POS_LC_EXP --version 2024.1 \
    --path FragHub_out/POS_LC_EXP/POS_LC.csv \
    --separation LC --fraghub-version 1.3.0

# ISDB, in-silico, positive mode — a second registration, not a second file
enpkg db spectral-library \
    --database gui_workspace/databases/enpkg.duckdb \
    --name ISDB_POS_LC --version 2024.1 \
    --path FragHub_out/ISDB_POS_LC_INSILICO/isdb_pos.csv \
    --separation LC
```

`--name` must be unique in the database and is what you will refer to in a run's
configuration, so choose something you will recognise later. Ion mode and
experimental/predicted are **not** arguments — they are read from the file.

Flags worth knowing:

| Flag | Effect |
|---|---|
| `--ms-level N` | Keep only spectra at this MS level. Defaults to `2`, because a FragHub export carries MS2–MS4 in one file and matching experimental MS2 against library MS3/MS4 is meaningless. `0` keeps every level. |
| `--replace` | Replace a library already registered under `--name`. Without it, a repeat registration is refused. |
| `--require-inchikey` | Fail if any spectrum lacks an InChIKey. The default warns and counts them; those spectra can be matched but never reach a compound. |
| `--separation` | `LC` or `GC`. Recorded for your benefit; FragHub keeps it only in the bucket name, so it cannot be read from the file. |
| `--fraghub-version` | Which FragHub produced the export. Worth recording — it is what you will want when an export's columns change. |

### 4.4 Confirm what is registered

```bash
enpkg db spectral-library --database gui_workspace/databases/enpkg.duckdb --list
```

```
 id  name                         version        mode  kind            spectra
---------------------------------------------------------------------------
  1  FragHub_POS_LC_EXP           2024.1         pos   experimental     289429
  2  ISDB_POS_LC                  2024.1         pos   in-silico        170000
```

Those names are the strings a configuration uses.

---

## 5. Using it in a run

Two settings on the MS1/MS2 configuration, answering different questions.

**`duckdb_path` — which file.** One path, because a single database holds both the LOTUS
tables and every registered library. MS1 and MS2 share one configuration for this reason.

**`spectral_libraries` — which libraries inside that file.** A list of registered names.
Leave it empty to search every registered library matching the run's ionization mode.

```yaml
ms_enhancer:
  duckdb_path: gui_workspace/databases/enpkg.duckdb
  spectral_libraries:
    - FragHub_POS_LC_EXP        # experimental only; omit to include ISDB too
```

A name that is not registered fails when the store is constructed, before any block runs,
rather than silently searching nothing.

In the graphical interface these are on the Pipeline page under **MS1 / MS2 settings
(shared)**; `spectral_libraries` is a text box taking one name per line.

### Why you might restrict the list

Registering ISDB alongside an experimental library and then choosing between them per run
is the point of the design. An in-silico library greatly increases the number of candidate
structures and therefore the number of annotations, at lower confidence than an
experimental match. Keeping them as separate registrations means you can run
experimental-only for a conservative annotation, or both for coverage, without rebuilding
anything — and the registry records which is which, so `predicted` is visible rather than
inferred from a name.

---

## 6. Updating a library

Registrations are replaced, not merged:

```bash
enpkg db spectral-library \
    --database gui_workspace/databases/enpkg.duckdb \
    --name FragHub_POS_LC_EXP --version 2025.1 \
    --path FragHub_out/POS_LC_EXP/POS_LC.csv \
    --separation LC --replace
```

Keep `--name` stable across versions and change `--version`: any configuration referring
to that name keeps working, and `--list` shows which version is loaded.

---

## 7. Troubleshooting

**`Catalog Error: Table with name spectral_library_registry does not exist!`**

The database predates the registry — it was built before spectral libraries were
registered individually, and holds a single `spectral_library` table instead of
`spectral_library_registry` + `library_spectra`. The old table also lacks columns the
matcher now reads (`inchikey`, `smiles`, `npc_*`, `classyfire_*`, `splash`, `ms_level`),
so it cannot be migrated — those values are not there to recover. Re-import from a FragHub
export as in §4. LOTUS is unaffected and does not need rebuilding.

The failure appears during step construction, before any block runs, so the run's summary
log is empty and no Turtle file is written.

**`DuckDB at … has no registered spectral library.`**

LOTUS is present but no library has been imported. §4.

**`… mixes 2 ionization modes` / `mixes predicted and experimental spectra`**

The CSV is not a single FragHub bucket. Export per bucket and import each separately.

**Only want to run without MS2 for now?**

Untick the `ms2` block. It is the only block requiring `spectral_library_store`;
taxonomical, network, ms1_graph, ms1, sirius and weights need at most `lotus_store`.

---

## 8. Where the files live

`gui_workspace/databases/` is the default the graphical interface uses and is created for
you. Nothing requires the database to be there — `duckdb_path` can point anywhere, and the
command line takes `--database` explicitly. The LOTUS CSVs and the FragHub exports are
inputs to the build step only: once imported, they are not needed at run time and can stay
off the machine that runs the pipeline.
