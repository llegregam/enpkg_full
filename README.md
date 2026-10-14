![release_badge](https://img.shields.io/github/v/release/enpkg/enpkg_full)
![license](https://img.shields.io/github/license/enpkg/enpkg_full)

# Welcome to the ENPKG Full Workflow!

<p align="center">
 <img src="https://github.com/enpkg/enpkg_workflow/blob/main/logo/enpkg_logo_full.png" width="400">
</p>


🌟 We're delighted to have you explore our computational workflow. This guide will walk you through the installation, setup, and execution of the ENPKG full workflow.
Interested in the science behind ENPKG? Check out the paper (https://doi.org/10.1021/acscentsci.3c00800) ! It's packed with insights and methodologies that power this workflow.

## 🧭 Repository status & architecture

The workflow is being re-implemented as an in-memory, object-oriented pipeline
under [`enpkg/monolith/`](enpkg/monolith/), replacing the older per-sample shell
scripts. The current design:

- **Data model** ([`data/`](enpkg/monolith/data/)) — an `Analysis` aggregates a
  sample's `AnnotatedSpectrum` list, MS1 adduct hypotheses, MS2 spectral-library
  matches, OpenTree taxonomy matches, and the molecular network.
- **Enhancers** ([`enhancers/`](enpkg/monolith/enhancers/)) — composable steps
  that each implement one uniform contract, `enhance(analysis) -> Analysis`:
  MS1 adducting, MS2 (ISDB) cosine matching, molecular networking, taxonomy
  resolution, SIRIUS, taxonomy/network-aware reweighting (label propagation), and
  MS2LDA 2.0 Mass2Motifs.
- **Registry + runner** ([`pipeline/blocks.py`](enpkg/monolith/pipeline/blocks.py),
  [`pipeline/runner.py`](enpkg/monolith/pipeline/runner.py)) — a single `BLOCKS` registry
  is the source of truth for which blocks exist, how to build each enhancer, and
  when it can run; the runner executes selected blocks in canonical order.
- **Reference data** — LOTUS compound/taxonomy metadata and spectral libraries are
  resolved from a persistent **DuckDB** file (see [`loaders/`](enpkg/monolith/loaders/);
  build it with `enpkg db lotus` and `enpkg db spectral-library` — see
  [docs/BUILDING_THE_DATABASE.md](docs/BUILDING_THE_DATABASE.md)).
- **Output** — an RDF/Turtle knowledge graph via [`rdf/`](enpkg/monolith/rdf/)
  (`AnalysisSerializer`), mapped to the EMI vocabulary.

## Running it

The `enpkg` command line is the primary entry point:

```bash
enpkg blocks list                       # what the pipeline can do
enpkg config init --out config.yaml     # a template to edit
enpkg config validate config.yaml       # check it before a long run
enpkg run   --config config.yaml --input-dir data/    # one experiment
enpkg batch --config config.yaml --parent-dir data/   # many experiments
enpkg batch discover --parent-dir data/ # preview what a batch would pick up
enpkg batch --config config.yaml --parent-dir data/ --experiments-file chosen.txt
                                        # only the run names listed, one per line
enpkg serialize run/analysis.pkl -o rdf_out/          # re-export without re-running
enpkg motifs fit data/ --out motifs/model --n-motifs 200 --n-iterations 1000 --ionization-mode pos
                                        # fit the MS2LDA model the mass2motif block reads
```

`--output-dir` puts every log, Turtle export and pickle from a run under one directory;
`--json-out` additionally writes a machine-readable summary of what ran, for scripting.

### The graphical interface

```bash
poetry install --with webui     # optional dependency group
enpkg gui                       # in a browser tab
enpkg gui --native              # in a desktop window
```

Three pages: choosing input data, configuring and running the pipeline, and the RDF
serializer options. A run started here executes as a separate process running the same
`enpkg run` shown above, so the two interfaces cannot drift apart — and output appears
while the run is going, it can be stopped, and it survives the browser being closed.

How it is wired: [`webui/ARCHITECTURE.md`](enpkg/monolith/webui/ARCHITECTURE.md). A
Streamlit interface still exists under [`gui/`](enpkg/monolith/gui/) and is being retired
once the NiceGUI one has been used on a real dataset; see
[`gui/ARCHITECTURE.md`](enpkg/monolith/gui/ARCHITECTURE.md) for that one. Per-enhancer
notes are under [`docs/`](docs/).

### Tests

Run them with `pytest`: unit tests (`enpkg/tests/test_data`, `enpkg/tests/test_pipeline`,
`enpkg/tests/test_cli`) need no external data, and `enpkg/tests/test_webui` additionally
needs the `webui` group. Tests marked `@pytest.mark.integration` need the DuckDB file and
network access.

The integration suite runs against a sampled fixture database and one dataset,
both built by a single command from a full database you already have:

```bash
poetry run python -m enpkg.scripts.build_test_fixtures
```

That writes `enpkg/tests/.databases/enpkg_fixture.duckdb` (~58 MB, sampled from
the ~1.7 GB production database) and `enpkg/tests/data/`, both gitignored. Pass
`--source-db` / `--batch-dir` / `--sample-name` to build from somewhere else, and
`--force` to rebuild. Then:

```bash
poetry run pytest -m integration        # needs the fixtures above + network
poetry run pytest -m "not integration"  # the fast suite CI runs
```

> ⚠️ The shell-script instructions in the **Launching the Workflow** section
> below describe the **legacy** per-sample workflow and are retained for
> reference while the port completes. The installation steps that follow still
> apply.

## 🌱 Getting Started

### Clone the repository

First, clone the repository to your local machine:

```bash
git clone https://github.com/enpkg/enpkg_full.git

```

Navigate to the newly created folder:

```bash
cd enpkg_full
```


### Install the required environment

**Requires Python 3.13 or 3.14.** Note that CPython 3.14.1 specifically is not supported —
`networkx` declares `!=3.14.1` for that release — so use 3.13, 3.14.0, or 3.14.2 and later.

We offer both `Mamba` or `Poetry` installation solutions, see below:

#### Mamba

Start your journey by setting up the required environment. It's a breeze (or not) with Mamba! See the [Mamba documentation](https://mamba.readthedocs.io/en/latest/) for more details.

```bash
mamba env create -f environment.yml
```

#### Poetry

First, see the [Poetry documentation](https://python-poetry.org/docs/) for more details.

```bash
poetry install
```

### Activate the environment

#### Mamba

Once the environment is ready, bring it to life with this simple command:

```bash
conda activate enpkg_full
```

#### Poetry

```bash
poetry shell
```

## 🌐 Install Sirius Locally


Check details at https://boecker-lab.github.io/docs.sirius.github.io/install/

To get the latest version for your platform, run the `install_sirius.sh` script specifying the path chosen for the installation. For example, from the root of the repository:

```bash
bash src/install_sirius.sh /home/username/sirius
```

Once Sirius is installed, you will need to precise the path to the executable see section [Editing config files](#-editing-config-files).


## 🧩 Mass2Motifs (MS2LDA)

The `mass2motif` block attaches MS2LDA 2.0 Mass2Motifs to features. MS2LDA needs Python
< 3.13 and older pandas and matchms than this project, so it runs in its own environment,
which `enpkg motifs fit` starts as a separate process. See
[docs/MASS2MOTIF_ENHANCER.md](docs/MASS2MOTIF_ENHANCER.md) for what it computes.

### Set up the MS2LDA environment (once)

```bash
conda create -n ms2lda python=3.12
conda activate ms2lda
pip install ms2lda==2.0.1 "matchms>=0.27,<0.30" "numpy==1.26.4"
ms2lda --only-download     # Spec2Vec model and library for MAG: 2 GB download, 6.1 GB on disk
```

The matchms and numpy pins are those of MS2LDA's own repository; without them pip installs
a matchms whose removal of `matchms.Spikes` makes `import MS2LDA` fail. Skip
`--only-download` if you fit with `--no-mag`.

Then tell `enpkg` where that environment's interpreter is, either with `--ms2lda-python`
on each fit or once with an environment variable:

```bash
export MS2LDA_PYTHON=/path/to/envs/ms2lda/bin/python   # Windows: ...\envs\ms2lda\python.exe
```

### Prepare a model

The block does not run MS2LDA. It reads a **model folder** that `enpkg motifs fit` writes
beforehand. MS2LDA learns its motifs from all experiments at once, while the pipeline
processes one experiment at a time, so the fit is a separate step. A model folder holds the
motifs and, for every spectrum the model was trained on, the motifs it contains. Prepare one
per batch of experiments, before running the pipeline on that batch.

**1. Lay out the batch folder** as for `enpkg batch`: one subfolder per experiment, each
holding one spectra file and one `*_quant.csv`, and the shared metadata file at the top.
MS2LDA reads `.mgf` and `.mzML` spectra files, not `.mzXML`. The run name of an experiment is
its spectra file name without the extension. Check what will be found with:

```bash
enpkg batch discover --parent-dir data/
```

This prints each experiment's run name in the first column, e.g. `arnica_5_pos_merged`.

**2. Decide which experiments go into the model.** Every experiment of the folder is used,
except:

- runs whose metadata `sample_type` is anything other than `sample`, such as blanks and QCs;
- runs the metadata does not list in its `sample_filename_pos` (or `_neg`) column; a warning
  names each one.

To fit on a subset, list run names, one per line, in a file passed with `--experiments-file`
— the run names `enpkg batch discover` printed, not the subfolder paths. An unknown name
fails the fit before anything is loaded, naming each one it couldn't match.
A model covers one ionization mode: fit positive and negative experiments separately. Only
the experiments in the fit receive motifs when the pipeline runs, so include every
experiment you will want annotated.

**3. Choose the parameters.**

| Flag | Meaning |
|---|---|
| `--out` | The model folder to create. It must not exist yet or be empty. |
| `--n-motifs` | Number of motifs (K). There is no rule for choosing it, and the motifs change with it. Our test fits used 20 for 2 experiments (2,260 spectra) and 200 for 43 experiments (about 100,000 spectra). |
| `--n-iterations` | Maximum number of training iterations. Training stops earlier when MS2LDA's convergence check is met. With a limit of 1,000, our 2-experiment fit stopped after 900; the 43-experiment fit used all 1,000. |
| `--ionization-mode` | `pos` or `neg`, matching the experiments. |
| `--no-mag` | Skip Motif Annotation Guidance, which proposes library molecules containing each motif's substructure. MAG exists for positive mode only, so negative-mode fits always run without it. |
| `--viz` | Also write MS2LDA's own figures, motif network and the file its viewer MS2LDAViz opens. These are not used by the pipeline and take most of the time on large fits. |
| `--ms2lda-config` | A JSON file of MS2LDA's own parameters, in the format of its `example_config.json`, merged over MS2LDA's defaults. |

For example, this `--ms2lda-config` file keeps spectra with at least 3 peaks (MS2LDA's
default is 5) and drops fragment and loss bins found in fewer than 5 spectra (default 3):

```json
{
  "preprocessing_parameters": {"min_frags": 3},
  "model_parameters": {"min_df": 5}
}
```

**4. Run the fit.** A trial on two or three experiments with few motifs checks the setup
first; on two experiments with 20 motifs the whole fit took under a minute.

```bash
enpkg motifs fit data/ --out motifs/trial --n-motifs 20 --n-iterations 1000 \
    --ionization-mode pos --experiments-file trial.txt
enpkg motifs fit data/ --out motifs/model --n-motifs 200 --n-iterations 1000 --ionization-mode pos
```

For scale: 43 experiments (about 100,000 spectra) with 200 motifs and `--viz` took 94 min and
16 GB of memory on a workstation, 62 min of it writing the `--viz` files. A fit of that size
without `--viz` has not been timed.

**5. Check the model.** The command ends by printing the model's id:
`Model 8a49c0a4463eea65 written to motifs/model`. If the fit fails, `fit.log` in the model
folder says why. `manifest.json` records what went in:

- `corpus.n_spectra_loaded` and `corpus.n_documents`: spectra read, and spectra kept after
  MS2LDA's cleaning, which drops for example spectra with fewer than 5 peaks;
- `corpus.n_documents_without_feature_id`: spectra without a `FEATURE_ID`. They shape the
  motifs but cannot be attached to a feature, so this should be 0;
- `convergence.iterations_run`: if it equals `--n-iterations`, training probably stopped at
  the limit before MS2LDA's convergence check was met; fit again with more iterations.

**6. Use it in the pipeline.** Give the block the model folder, in the configuration file or
in the Mass2Motifs settings on the Pipeline page:

```yaml
mass2motif:
  model_dir: motifs/model
  min_probability: 0.1     # minimum share of a feature's spectrum the motif explains
  min_overlap: 0.0         # minimum share of the motif found in the feature's spectrum
```

Then run the pipeline on the same batch folder, with the same ionization mode. Each
experiment's `summary.log` reports how many of its features received a motif. The block
leaves an experiment without motifs, with a warning, when the experiment was not in the fit
or when the model's ionization mode differs from the run's. It stops with an error when a
feature's precursor m/z differs from the one recorded in the fit, which happens when the
spectra file was re-exported after fitting.

**7. Fit again** after adding experiments or changing spectra files, into a new folder. A
fit that produces different motifs gets a new model id, and the knowledge graph names motifs
by model id and motif number. Motifs from two models are therefore different nodes, even
when they look alike.

## 🔐 Setting Up Environment Variables

Setting up the environment variables. To login to Sirius, you will need to set up the following environment variables (SIRIUS_USERNAME and SIRIUS_PASSWORD). You can do so launching the following command:

```bash
bash src/setup_sirius_env.sh
```


## 🛠 Editing Config Files

You will need to edit the following parameters files:

- Parameters at [user.yaml](https://github.com/enpkg/enpkg_full/blob/c8e649290ee72f000c3385e7669b5da2215abad8/params/user.yml)

All parameters are commented and should be self-explanatory.


### E.g. selection of the dataset to process

For example you can enter the record_id and record_name of a Zenodo dataset on [line](https://github.com/enpkg/enpkg_full/blob/c8e649290ee72f000c3385e7669b5da2215abad8/params/user.yml#L8).
As it is set up here, this will download a small test dataset (https://doi.org/10.5281/zenodo.10018590).

## Prepare the metadata

First we extracts the sha from the .mzML files and create the metadata file. This is done by running the following command:

```bash
python extract_sha.py /home/allardpm/EMIKG/dbgi_batch_00001/raw /home/allardpm/git_repos/DBGI/dbgi-metabolomics/docs/dbgi_project_00001/dbgi_batch_00001/metadata/treated/dbgi_batch_00001_sha.csv
```

Then we create the metadata file for the dataset. This is done by running the following command:

```bash
python merge_sha_metadata.py /home/allardpm/git_repos/DBGI/dbgi-metabolomics/docs/dbgi_project_00001/dbgi_batch_00001/metadata/treated/dbgi_batch_00001_metadata.tsv /home/allardpm/git_repos/DBGI/dbgi-metabolomics/docs/dbgi_project_00001/dbgi_batch_00001/metadata/treated/dbgi_batch_00001_sha.csv /home/allardpm/git_repos/DBGI/dbgi-metabolomics/docs/dbgi_project_00001/dbgi_batch_00001/metadata/treated/dbgi_batch_00001_metadata_sha.tsv
```

## 🚀 Launching the Workflow

From the root of the repository, run:

```bash
sh workflow/00_workflow_all.sh
```

On the previous test dataset, this should take about 10 minutes to run.

## 🎉 Explore your newly generated Knowledge Graph

You can use GraphDB to explore the generated Knowledge Graph. To do so, you will need to install GraphDB (https://graphdb.ontotext.com/download/) and import the generated .ttl files.
Make sure to read the latest Graph DB documentation (https://graphdb.ontotext.com/documentation/) to get started.


## 🌟 Your Feedback Matters

Facing an Issue? Encountering a glitch or have a suggestion? Your input is crucial for us. Here’s how you can help:

- Report Issues: Use the 'Issues' tab in our GitHub repo to report any problems or ideas.
- Detailed Descriptions Help: Include as much detail as possible - error messages, steps to reproduce, and screenshots are all super helpful.
- Stay Updated: We’ll keep you in the loop as we work on fixing the issue or considering your suggestion.


## 🔎 Explore the ENPKG graph 

A Knowledge Graph, has been build on a collection of 1600 tropical plants extracts (https://doi.org/10.1093/gigascience/giac124). This KG also integrates data from a metabolomics study led over 337 medicinal plants of the Korean Pharmacopeia (https://doi.org/10.1038/s41597-022-01662-2). It can be explored following these links.

- The ENPKG graph is available at the following address https://enpkg.commons-lab.org/graphdb/. No need for login !
- The SPARQL research interface can be reached at https://enpkg.commons-lab.org/graphdb/sparql. Make sure to check the [paper](https://doi.org/10.1021/acscentsci.3c00800) for examples of queries.
- The ENPKG vocabulary is described at https://enpkg.commons-lab.org/doc/index.html


## 📜 Citations 

This set of script represents a pipeline calling multiple tools. Please make sure to cite the original authors of the tools used in this workflow.

[Allard et al. 2016, Analytical Chemistry](https://pubs.acs.org/doi/10.1021/acs.analchem.5b04804)  
[Davies et al. 2015, Nucleic Acids Research](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4489243/)  
[Djoumbou et al. 2016, Journal of Cheminformatics](https://jcheminf.biomedcentral.com/articles/10.1186/s13321-016-0174-y)  
[Dührkop et al. 2015, Nature Methods](https://www.pnas.org/doi/full/10.1073/pnas.1509788112)  
[Dührkop et al. 2019, Nature Methods](https://www.nature.com/articles/s41592-019-0344-8)  
[Dührkop et al. 2020, Nature Biotechnology](https://www.nature.com/articles/s41587-020-0740-8)  
[Gaudry et al. 2022, Frontiers in Bioinformatics](https://www.frontiersin.org/articles/10.3389/fbinf.2022.842964/full)  
[Hoffmann et al. 2022, Nature Biotechnology](https://www.nature.com/articles/s41587-021-01045-9)  
[Huber et al. 2020, Journal of Open Source Software](https://joss.theoj.org/papers/10.21105/joss.02411)  
[Kim et al. 2021, Journal of Natural Products](https://pubs.acs.org/doi/10.1021/acs.jnatprod.1c00399)  
[Ludwig et al. 2020, Nature Machine Intelligence](https://pubs.acs.org/doi/10.1021/acs.jnatprod.1c00399)  
[McTavish et al. 2021, Systematic Biology](https://academic.oup.com/sysbio/article/70/6/1295/6273200)  
[Rutz et al. 2016, Frontiers in Plant Science](https://www.frontiersin.org/articles/10.3389/fpls.2019.01329/full)  
[Rutz et al. 2022, Elife](https://elifesciences.org/articles/70780)  

To cite the ENPKG workflow, please use the following reference [Gaudry et al. 2023](https://doi.org/10.1021/acscentsci.3c00800).


## 📋 Note

This workflow describes a pilot application aiming to transition from classical metabolomics datasets to [Linked Open Data](https://5stardata.info/en/) in such datasets. It is currently being ported to the more generic EMIKG framework (https://github.com/earth-metabolome-initiative/emikg) that we are developping in the frame of the Earth Metabolome Initiative (http://www.earthmetabolome.org/). Stay tuned.
