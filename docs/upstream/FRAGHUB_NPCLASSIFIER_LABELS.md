# FragHub: corrupted NPClassifier labels in the shipped ontology table

This file holds an issue for the FragHub repository
(<https://github.com/eMetaboHUB/FragHub>), ready to paste, followed by notes for a pull
request that repairs the table. Our own importer works around the problem in the
meantime: see `enpkg/monolith/loaders/spectral_libraries/npc_labels.py`.

Everything in the issue was measured on FragHub at commit `82c89b3` (2025-11-18) unless it
says otherwise.

---

## Issue

**Title:** NPClassifier labels in `ontologies_dict_part_*.csv` are corrupted ("nan"/"none" deleted inside names, names split at commas)

### Summary

The NPClassifier columns of the ontology table FragHub ships
(`datas/ontologies_datas/ontologies_dict_part_1.csv` … `_5.csv`) contain corrupted labels:
`Ligs` instead of `Lignans`, `Flavas` instead of `Flavanones`, and
`Lanostane|Tirucallane and Euphane triterpenoids` instead of
`Lanostane, Tirucallane and Euphane triterpenoids`. `ontologies_completion()` merges this
table onto the spectra by InChIKey, so the corrupted labels reach every FragHub export.

88,825 of the table's 1,001,751 InChIKeys (8.9%) carry at least one corrupted NPClassifier
label: 14,913 at superclass level and 88,291 at class level. Pathways are not affected. The
ClassyFire columns of the same table are not affected either.

### Two rules account for every corrupted label

1. **The substrings `nan` and `none` are deleted wherever they occur inside a label.**
   `Lignans` → `Ligs`, `Pregnane steroids` → `Prege steroids`,
   `Flavanones` → `Flavas`, `Anthraquinones and anthrones` → `Anthraquis and anthrones`.
2. **A label containing `", "` is split there**, and the pieces are stored as separate
   `|`-separated labels. `Carotenoids (C40, β-β)` → `Carotenoids (C40|β-β)`,
   `Indole diketopiperazine alkaloids (L-Trp, L-Pro)` →
   `Indole diketopiperazine alkaloids (L-Trp|L-Pro)`.

Checked against NPClassifier's term list: every superclass and class whose name contains
`nan`, `none` or `", "` (2 superclasses, 84 classes) is corrupted wherever it occurs; none of
them appears intact anywhere in the table. The full list is at the end of this issue.

### How to reproduce

From the repository root:

```python
import glob

import pandas as pd

files = sorted(glob.glob("datas/ontologies_datas/ontologies_dict_part_*.csv"))
df = pd.concat((pd.read_csv(f, sep=";", encoding="UTF-8") for f in files), ignore_index=True)
print(f"{len(df):,} InChIKeys")


def labels(column):
    return df[column].dropna().astype(str).str.split("|").explode()


# 1. No NPClassifier label contains "nan"; ClassyFire labels in the same rows often do.
for column in ["NPCLASS_PATHWAY", "NPCLASS_SUPERCLASS", "NPCLASS_CLASS",
               "CLASSYFIRE_SUPERCLASS", "CLASSYFIRE_CLASS", "CLASSYFIRE_SUBCLASS"]:
    n = df[column].dropna().astype(str).str.contains("nan", case=False).sum()
    print(f"{column:22s} cells containing 'nan': {n:,}")

# 2. Corrupted labels are present; their correct spellings never are.
superclass, klass = labels("NPCLASS_SUPERCLASS"), labels("NPCLASS_CLASS")
for series, corrupted, correct in [
    (superclass, "Ligs", "Lignans"),
    (superclass, "Phethrenoids", "Phenanthrenoids"),
    (klass, "Flavas", "Flavanones"),
    (klass, "Anthraquis and anthrones", "Anthraquinones and anthrones"),
    (klass, "Lanostane", "Lanostane, Tirucallane and Euphane triterpenoids"),
]:
    print(f"{corrupted!r:28s} {(series == corrupted).sum():6,d}    {correct!r}: {(series == correct).sum():,}")
```

Output:

```
1,001,751 InChIKeys
NPCLASS_PATHWAY        cells containing 'nan': 0
NPCLASS_SUPERCLASS     cells containing 'nan': 0
NPCLASS_CLASS          cells containing 'nan': 0
CLASSYFIRE_SUPERCLASS  cells containing 'nan': 9,812
CLASSYFIRE_CLASS       cells containing 'nan': 15,746
CLASSYFIRE_SUBCLASS    cells containing 'nan': 7,063
'Ligs'                       13,474    'Lignans': 0
'Phethrenoids'                1,442    'Phenanthrenoids': 0
'Flavas'                      5,267    'Flavanones': 0
'Anthraquis and anthrones'    6,035    'Anthraquinones and anthrones': 0
'Lanostane'                   8,919    'Lanostane, Tirucallane and Euphane triterpenoids': 0
```

### Since when

Both versions of the table in the repository history carry identical corrupted labels:
`7cb091f` (2024-12-09, the table's first commit) and `1ea0796` (2025-09-01, "Update ontology
CSV files with revised data"). The 2025 revision changed the missing-value placeholder from
`UNKNOWN` to `NOT FOUND` and left the labels as they were.

### Likely cause

The script that generated the table is not in the repository, so this part is inferred from
the output only. NPClassifier returns a list of labels per rank, and both rules are what
happens when such lists are turned into text:

- Python and pandas print a missing value as `nan` or `None`. Removing those with a substring
  replacement such as `s.replace("nan", "")`, instead of testing the whole value, also removes
  the same letters from inside real names. The deletions seen are of lowercase `nan` and
  `none`, so the replacement was either lowercase or case-insensitive.
- Joining a list with `", "` and later splitting the string on `", "` gives back the original
  items only when no item contains `", "` itself. A handful of NPClassifier classes do.

The ClassyFire columns hold one label per rank, need no list handling, and are intact, which
is consistent with the fault being in the list-to-text step.

### Suggested fix

Regenerate the NPClassifier columns from NPClassifier's output, testing missing values as
whole values (for example `pd.isna(value)`) and joining multiple labels with `|` directly
from the list. If regenerating is not practical, the existing table can be repaired in place
from NPClassifier's own term list (`Classifier/dict/index_v1.json` in the NP-Classifier
repository), because each corrupted label maps back to exactly one term. We have a tested
script for that and can open a pull request.

### Why it matters downstream

A corrupted label matches no NPClassifier term, so a consumer that maps labels to the
NPClassifier vocabulary either drops it or creates a term that does not exist (`Lignans`
becomes an unknown `LIGS`). We found this while importing `FRAGHUB_POS_LC` (2026.03 export,
1,450,368 spectra) into a knowledge-graph pipeline, where 103,210 label occurrences were
affected.

<details>
<summary>Every corrupted label in the table, with the number of InChIKeys carrying it</summary>

Superclass (2 labels, 14,913 InChIKeys):

| Label in the table | NPClassifier term | InChIKeys |
|---|---|---|
| `Ligs` | `Lignans` | 13,474 |
| `Phethrenoids` | `Phenanthrenoids` | 1,442 |

Class (84 labels, 88,291 InChIKeys):

| Label in the table | NPClassifier term | InChIKeys |
|---|---|---|
| `Oleae triterpenoids` | `Oleanane triterpenoids` | 20,370 |
| `Lanostane\|Tirucallane and Euphane triterpenoids` | `Lanostane, Tirucallane and Euphane triterpenoids` | 8,919 |
| `Prege steroids` | `Pregnane steroids` | 8,048 |
| `Anthraquis and anthrones` | `Anthraquinones and anthrones` | 6,035 |
| `Neoligs` | `Neolignans` | 5,521 |
| `Flavas` | `Flavanones` | 5,267 |
| `Corythe type` | `Corynanthe type` | 4,121 |
| `Naphthoquis` | `Naphthoquinones` | 3,499 |
| `Cadie sesquiterpenoids` | `Cadinane sesquiterpenoids` | 2,790 |
| `Prenyl qui meroterpenoids` | `Prenyl quinone meroterpenoids` | 1,841 |
| `Arylnaphthalene and aryltetralin ligs` | `Arylnaphthalene and aryltetralin lignans` | 1,780 |
| `Isoflavas` | `Isoflavanones` | 1,675 |
| `Phethrenes` | `Phenanthrenes` | 1,426 |
| `Dibenzocyclooctadienes ligs` | `Dibenzocyclooctadienes lignans` | 1,303 |
| `Furanoid ligs` | `Furanoid lignans` | 1,202 |
| `Carotenoids (C40\|β-β)` | `Carotenoids (C40, β-β)` | 1,138 |
| `Furofuranoid ligs` | `Furofuranoid lignans` | 1,127 |
| `Dibenzylbutyrolactone ligs` | `Dibenzylbutyrolactone lignans` | 898 |
| `Daphe diterpenoids` | `Daphnane diterpenoids` | 884 |
| `Zearales` | `Zearalenones` | 875 |
| `Dibenzylbutane ligs` | `Dibenzylbutane lignans` | 698 |
| `Pie monoterpenoids` | `Pinane monoterpenoids` | 686 |
| `Minor ligs` | `Minor lignans` | 648 |
| `Morphi alkaloids` | `Morphinan alkaloids` | 592 |
| `Fere and Arborie triterpenoids` | `Fernane and Arborinane triterpenoids` | 557 |
| `Pulvis` | `Pulvinones` | 509 |
| `Carotenoids (C40\|Ψ-Ψ)` | `Carotenoids (C40, Ψ-Ψ)` | 507 |
| `Ingee diterpenoids` | `Ingenane diterpenoids` | 482 |
| `Flavonoligs` | `Flavonolignans` | 434 |
| `Indole diketopiperazine alkaloids (L-Trp\|L-Pro)` | `Indole diketopiperazine alkaloids (L-Trp, L-Pro)` | 401 |
| `Carotenoids (C40\|β-Ψ)` | `Carotenoids (C40, β-Ψ)` | 391 |
| `Carotenoids (C40\|β-ε)` | `Carotenoids (C40, β-ε)` | 370 |
| `Indole diketopiperazine alkaloids (L-Trp\|L-Ala)` | `Indole diketopiperazine alkaloids (L-Trp, L-Ala)` | 350 |
| `Nardosie sesquiterpenoids` | `Nardosinane sesquiterpenoids` | 331 |
| `Naphthales` | `Naphthalenones` | 331 |
| `Longipie sesquiterpenoids` | `Longipinane sesquiterpenoids` | 325 |
| `Myrsie diterpenoids` | `Myrsinane diterpenoids` | 272 |
| `Hasuba alkaloids` | `Hasubanan alkaloids` | 205 |
| `Benzoquis` | `Benzoquinones` | 197 |
| `Stilbenoligs` | `Stilbenolignans` | 187 |
| `Fukie sesquiterpenoids` | `Fukinane sesquiterpenoids` | 186 |
| `Coumarinoligs` | `Coumarinolignans` | 167 |
| `Benzophes` | `Benzophenones` | 153 |
| `Africae sesquiterpenoids` | `Africanane sesquiterpenoids` | 150 |
| `Asbestie diterpenoids` | `Asbestinane diterpenoids` | 145 |
| `Apocarotenoids (C30\|Ψ-Ψ)` | `Apocarotenoids (C30, Ψ-Ψ)` | 141 |
| `Mulie diterpenoids` | `Mulinane diterpenoids` | 140 |
| `Pachysae triterpenoids` | `Pachysanane triterpenoids` | 139 |
| `Glutie triterpenoids` | `Glutinane triterpenoids` | 138 |
| `Carotenoids (C40\|β-κ)` | `Carotenoids (C40, β-κ)` | 127 |
| `Indole diketopiperazine alkaloids (L-Trp\|L-Trp)` | `Indole diketopiperazine alkaloids (L-Trp, L-Trp)` | 97 |
| `Pentalee sesquiterpenoids` | `Pentalenane sesquiterpenoids` | 96 |
| `Silphie sesquiterpenoids` | `Silphinane sesquiterpenoids` | 95 |
| `Iphioe sesquiterpenoids` | `Iphionane sesquiterpenoids` | 80 |
| `Valeree sesquiterpenoids` | `Valerenane sesquiterpenoids` | 79 |
| `Gorgoe sesquiterpenoids` | `Gorgonane sesquiterpenoids` | 71 |
| `Carotenoids (C40\|ε-Ψ)` | `Carotenoids (C40, ε-Ψ)` | 64 |
| `Shioe triterpenoids` | `Shionane triterpenoids` | 63 |
| `Campheree sesquiterpenoids` | `Campherenane sesquiterpenoids` | 63 |
| `Carotenoids (C50\|Ψ-Ψ)` | `Carotenoids (C50, Ψ-Ψ)` | 61 |
| `Premyrsie diterpenoids` | `Premyrsinane diterpenoids` | 56 |
| `Adiae triterpenoids` | `Adianane triterpenoids` | 52 |
| `Carotenoids (C40\|ε-ε)` | `Carotenoids (C40, ε-ε)` | 46 |
| `Carotenoids (C40\|β-π)` | `Carotenoids (C40, β-π)` | 40 |
| `Bourboe sesquiterpenoids` | `Bourbonane sesquiterpenoids` | 38 |
| `Carotenoids (C50\|ε-ε)` | `Carotenoids (C50, ε-ε)` | 35 |
| `Longibore sesquiterpenoids` | `Longibornane sesquiterpenoids` | 24 |
| `Carotenoids (C40\|π-Ψ)` | `Carotenoids (C40, π-Ψ)` | 23 |
| `Carotenoids (C50\|β-Ψ)` | `Carotenoids (C50, β-Ψ)` | 22 |
| `Carotenoids (C40\|β-Χ)` | `Carotenoids (C40, β-Χ)` | 21 |
| `Carotenoids (C40\|π-π)` | `Carotenoids (C40, π-π)` | 20 |
| `Carotenoids (C40\|κ-κ)` | `Carotenoids (C40, κ-κ)` | 15 |
| `Carotenoids (C40\|Χ-Ψ)` | `Carotenoids (C40, Χ-Ψ)` | 14 |
| `Carotenoids (C45\|ε-Ψ)` | `Carotenoids (C45, ε-Ψ)` | 14 |
| `Carotenoids (C50\|γ-γ)` | `Carotenoids (C50, γ-γ)` | 13 |
| `Carotenoids (C40\|γ-ε)` | `Carotenoids (C40, γ-ε)` | 12 |
| `Carotenoids (C40\|β-γ)` | `Carotenoids (C40, β-γ)` | 11 |
| `Carotenoids (C50\|β-β)` | `Carotenoids (C50, β-β)` | 11 |
| `Carotenoids (C40\|γ-Ψ)` | `Carotenoids (C40, γ-Ψ)` | 10 |
| `Carotenoids (C40\|π-Χ)` | `Carotenoids (C40, π-Χ)` | 9 |
| `Carotenoids (C40\|κ-Χ)` | `Carotenoids (C40, κ-Χ)` | 8 |
| `Carotenoids (C45\|Ψ-Ψ)` | `Carotenoids (C45, Ψ-Ψ)` | 5 |
| `Carotenoids (C40\|Χ-Χ)` | `Carotenoids (C40, Χ-Χ)` | 3 |
| `Carotenoids (C45\|β-Ψ)` | `Carotenoids (C45, β-Ψ)` | 3 |

</details>

---

## Notes for a pull request

Not part of the issue. These are for proposing the in-place repair to FragHub later.

### Where the table is read

`scripts/globals_vars.py` concatenates every `ontologies_dict*` file under
`datas/ontologies_datas/` with `pd.read_csv(..., sep=";", encoding="UTF-8")` and no
post-processing; `scripts/ontologies_completion.py` merges it onto the spectra by InChIKey
with `pd.merge` and `combine_first`. A repair of the CSV parts is therefore enough: no code
in FragHub needs to change.

### Repair script

Takes the folder of CSV parts and NPClassifier's `index_v1.json` (from
<https://github.com/mwang87/NP-Classifier>, `Classifier/dict/`), and rewrites the parts in
place.

```python
"""Restore the NPClassifier labels in FragHub's ontologies_dict_part_*.csv in place.

Usage: python repair_fraghub_ontology.py <ontologies_datas folder> <NP-Classifier index_v1.json>
"""
import glob
import json
import sys
from pathlib import Path

import pandas as pd

RANK_OF_COLUMN = {
    "NPCLASS_PATHWAY": "Pathway",
    "NPCLASS_SUPERCLASS": "Superclass",
    "NPCLASS_CLASS": "Class",
}


def rewritten_form(term):
    """The labels the corrupted table holds in place of an NPClassifier term."""
    return tuple(term.replace("nan", "").replace("none", "").split(", "))


def build_index(terms):
    """Rewritten form -> term, for the terms the corruption alters, keeping unique forms."""
    sources = {}
    for term in terms:
        form = rewritten_form(term)
        if form != (term,):
            sources.setdefault(form, set()).add(term)
    return {form: next(iter(found)) for form, found in sources.items() if len(found) == 1}


def repair_cell(cell, originals, genuine, longest):
    """Replace runs of neighbouring labels that match a rewritten form with the term."""
    labels = cell.split("|")
    restored, i = [], 0
    while i < len(labels):
        for length in range(min(longest, len(labels) - i), 0, -1):
            run = tuple(labels[i:i + length])
            term = originals.get(run)
            if term is not None and not any(label in genuine for label in run):
                restored.append(term)
                i += length
                break
        else:
            restored.append(labels[i])
            i += 1
    return "|".join(restored)


def main(folder, index_path):
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    rules = {}
    for column, rank in RANK_OF_COLUMN.items():
        genuine = set(index[rank])
        originals = build_index(genuine)
        rules[column] = (originals, genuine, max((len(f) for f in originals), default=1))
    for path in sorted(glob.glob(str(Path(folder) / "ontologies_dict_part_*.csv"))):
        df = pd.read_csv(path, sep=";", encoding="UTF-8", dtype=str, keep_default_na=False)
        changed = 0
        for column, (originals, genuine, longest) in rules.items():
            before = df[column]
            df[column] = before.map(lambda c: repair_cell(c, originals, genuine, longest))
            changed += int((before != df[column]).sum())
        df.to_csv(path, sep=";", index=False, encoding="UTF-8")
        print(f"{Path(path).name}: {changed} cells restored")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

Two properties keep it safe. A run containing a label that is itself an NPClassifier term is
never replaced, so a correct label cannot be rewritten. A split fragment is restored only
together with its neighbouring partner, since on its own it names no single term
(`Carotenoids (C40` could be any of 19 classes).

### What it did on a copy of the table at `82c89b3`

- 103,204 cells restored: 14,913 superclass, 88,291 class; 0 in every other column.
- Row count unchanged (1,001,751); `INCHIKEY` and the three ClassyFire columns identical.
- The reproduction snippet above then prints `'Ligs' 0 / 'Lignans' 13,474`,
  `'Flavas' 0 / 'Flavanones' 5,267`, `'Lanostane' 0 / ... 8,919`, and the NPClassifier
  superclass and class columns contain `nan` in 14,913 and 55,675 cells.
- Byte comparison of `ontologies_dict_part_1.csv` before and after: same line count
  (200,353), same CRLF line endings, no added quoting, and all 17,842 changed lines differ
  only in the NPClassifier fields. The other four parts were compared by parsed value only.
