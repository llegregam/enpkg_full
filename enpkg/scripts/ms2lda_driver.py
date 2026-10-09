"""Fit an MS2LDA 2.0 model and write the model folder that enpkg reads.

This file runs under the Python interpreter of a separate environment in which
``ms2lda`` 2.0.1 is installed (Python 3.11 or 3.12). enpkg starts it as a separate
process through ``enpkg motifs fit`` and never imports it, so it must not import
``enpkg``, and its syntax must stay valid for Python 3.11. Every MS2LDA import sits
inside a function, which lets enpkg's tests import this module without MS2LDA.

Usage::

    <ms2lda python> ms2lda_driver.py --check <model_dir>/request.json
    <ms2lda python> ms2lda_driver.py <model_dir>/request.json

``request.json`` is written by ``enpkg.scripts.fit_mass2motifs``. Its keys:
``out_dir``, ``ionization_mode``, ``n_motifs``, ``n_iterations``, ``mag`` (bool),
``viz`` (bool), ``ms2lda_config`` (an MS2LDA-format parameter dict, possibly empty)
and ``experiments`` (a list of ``{"run_name", "spectra_path"}``).

The fit calls MS2LDA's own functions in the order ``MS2LDA.run.run`` calls them:
``clean_spectra`` -> ``features_to_words`` -> ``define_model`` -> ``train_model`` ->
``extract_motifs`` -> ``create_motif_spectra`` -> MAG (``s2v_annotation`` ->
``hit_clustering`` -> ``add_annotation`` -> ``motif_optimization``). Per-spectrum
memberships come from ``generate_corpusjson_from_tomotopy``, called with the same
arguments ``save_visualization_data`` uses, so they are the theta and overlap values
MS2LDA itself reports, with its cut-offs (theta > 0.01; beta > 0.001 and phi >= 0.01
inside the overlap sum). That function names document ``i`` ``spec_i``, and
``clean_spectra`` names the ``i``-th spectrum it keeps ``spec_i``; this file checks
the two agree before linking a document to the run name and feature id of its
spectrum.

Files written to ``out_dir`` (``manifest.json`` last, so its presence marks a
completed fit):

- ``motifs.json``: per motif, the top words of its topic as ``[m/z, beta]`` pairs
  (``fragments``, ``losses``; beta is the topic-word probability), and with MAG the
  optimised fragments and losses and the candidate molecules.
- ``documents.parquet``: one row per training document: ``doc_id``, ``run_name``,
  ``feature_id`` (string, null when the spectrum had none), ``precursor_mz``.
- ``memberships.parquet``: one row per (document, motif) with theta > 0.01:
  ``doc_id``, ``run_name``, ``feature_id``, ``motif``, ``probability``, ``overlap``.
- ``ms2lda/``: MS2LDA's trained model (``ms2lda.bin``), ``.m2m`` motif files and
  MotifDB JSON. With ``viz``, also its figures, motif network, document map and,
  when there are fewer than 500 motifs, ``ms2lda_viz.json.gz`` for MS2LDAViz.
- ``manifest.json``: software versions, merged parameters, experiments with the
  sha256 of their spectra files, corpus counts, convergence history, timings.
- ``fit.log``: this file's log.
"""

import argparse
import copy
import hashlib
import json
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The ms2lda distribution version whose internal functions this file calls.
# MS2LDA/__version__.py inside that distribution reads "2.0.0", so the distribution
# metadata is checked instead.
REQUIRED_MS2LDA_VERSION = "2.0.1"

# MS2LDA.run.run writes ms2lda_viz.json.gz only for fewer motifs than this; the
# same rule applies here.
VISUALIZATION_MAX_MOTIFS = 500

# Spec2Vec resources MAG reads, as named by annotation_parameters.
SPEC2VEC_KEYS = ("s2v_model_path", "s2v_library_embeddings", "s2v_library_db")

_VERSIONED_DISTRIBUTIONS = (
    "ms2lda", "tomotopy", "matchms", "numpy", "pandas", "spec2vec", "gensim",
    "faiss-cpu", "rdkit", "scikit-learn",
)

logger = logging.getLogger("ms2lda_driver")


# ---------------------------------------------------------------- pure helpers
def deep_update(base, update):
    """Merge ``update`` into ``base`` recursively and return ``base``.

    Nested dicts are merged key by key; any other value replaces the one in
    ``base``. The same rule as ``scripts.ms2lda_runfull.deep_update``.
    """
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def merged_parameters(default_params, request, spec2vec_dir):
    """MS2LDA parameters for this fit.

    Starts from a deep copy of ``default_params`` (MS2LDA's CLI defaults), merges the
    user's ``ms2lda_config`` over it, then sets what the request decides: the output
    folder, the motif set name, and the charge (+1 or -1 from the ionization mode)
    unless the user's config sets one. Spec2Vec paths the user did not set point into
    ``spec2vec_dir``, where ``ms2lda --only-download`` puts them; MS2LDA's own defaults
    are relative paths that only resolve from inside its source tree.
    """
    params = deep_update(copy.deepcopy(default_params), copy.deepcopy(request["ms2lda_config"]))
    user_dataset = request["ms2lda_config"].get("dataset_parameters", {})
    user_annotation = request["ms2lda_config"].get("annotation_parameters", {})

    dataset = params["dataset_parameters"]
    dataset["output_folder"] = str(Path(request["out_dir"]) / "ms2lda")
    dataset["name"] = Path(request["out_dir"]).name
    if "charge" not in user_dataset:
        dataset["charge"] = 1 if request["ionization_mode"] == "pos" else -1

    annotation = params["annotation_parameters"]
    for key in SPEC2VEC_KEYS:
        if key not in user_annotation:
            annotation[key] = str(Path(spec2vec_dir) / Path(annotation[key]).name)
    return params


def missing_spec2vec_files(annotation_parameters):
    """The Spec2Vec resource paths in ``annotation_parameters`` that do not exist."""
    return [annotation_parameters[key] for key in SPEC2VEC_KEYS
            if not Path(annotation_parameters[key]).is_file()]


def check_spectrum_ids(spectra):
    """Raise unless the ``i``-th spectrum carries the id ``spec_i``."""
    for index, spectrum in enumerate(spectra):
        if spectrum.get("id") != f"spec_{index}":
            raise RuntimeError(
                f"Cleaned spectrum {index} has id {spectrum.get('id')!r}, not 'spec_{index}': "
                "documents cannot be linked to spectra by position."
            )


def document_rows(spectra):
    """One row per cleaned spectrum, in document order."""
    rows = []
    for index, spectrum in enumerate(spectra):
        feature_id = spectrum.get("feature_id")
        precursor_mz = spectrum.get("precursor_mz")
        rows.append({
            "doc_id": f"spec_{index}",
            "run_name": spectrum.get("enpkg_run_name"),
            "feature_id": None if feature_id is None else str(feature_id),
            "precursor_mz": None if precursor_mz is None else float(precursor_mz),
        })
    return rows


def membership_rows(lda_dict, documents):
    """Flatten ``lda_dict['theta']`` into rows, with the matching overlap score.

    ``documents`` are the rows from :func:`document_rows`. Overlap is 0.0 for a
    (document, motif) pair that has theta above MS2LDA's cut-off but no word with
    phi >= 0.01 assigned to the motif.
    """
    by_doc = {row["doc_id"]: row for row in documents}
    overlaps = lda_dict["overlap_scores"]
    rows = []
    for doc_id, thetas in lda_dict["theta"].items():
        document = by_doc[doc_id]
        doc_overlaps = overlaps.get(doc_id, {})
        for motif_name, theta in thetas.items():
            rows.append({
                "doc_id": doc_id,
                "run_name": document["run_name"],
                "feature_id": document["feature_id"],
                "motif": int(motif_name.rsplit("_", 1)[1]),
                "probability": float(theta),
                "overlap": float(doc_overlaps.get(motif_name, 0.0)),
            })
    return rows


def _split_words(words):
    """Turn ``[("frag@70.04", beta), ...]`` into fragment and loss ``[m/z, beta]`` lists."""
    fragments, losses = [], []
    for word, beta in words:
        kind, value = word.split("@", 1)
        target = fragments if kind == "frag" else losses
        target.append([float(value), float(beta)])
    return fragments, losses


def _peaks(fragments):
    """``[[m/z, intensity], ...]`` from a matchms ``Fragments`` object (or None)."""
    if fragments is None:
        return []
    return [[float(mz), float(intensity)]
            for mz, intensity in zip(fragments.mz, fragments.intensities, strict=True)]


def motif_records(motif_words, optimized_motifs, clustered_smiles, clustered_scores, inchikey_of):
    """One JSON record per motif.

    ``optimized_motifs``, ``clustered_smiles`` and ``clustered_scores`` are None when
    MAG did not run. ``inchikey_of`` maps a SMILES string to its InChIKey, or None when
    the SMILES does not parse.
    """
    records = []
    for index, words in enumerate(motif_words):
        fragments, losses = _split_words(words)
        record = {
            "index": index,
            "name": f"motif_{index}",
            "fragments": fragments,
            "losses": losses,
            "optimized_fragments": [],
            "optimized_losses": [],
            "candidates": [],
        }
        if optimized_motifs is not None:
            optimized = optimized_motifs[index]
            record["optimized_fragments"] = _peaks(optimized.peaks)
            record["optimized_losses"] = _peaks(optimized.losses)
            record["candidates"] = [
                {"smiles": smiles, "inchikey": inchikey_of(smiles), "score": float(score)}
                for smiles, score in zip(clustered_smiles[index], clustered_scores[index], strict=True)
            ]
        records.append(record)
    return records


def write_json(path, payload):
    """Write ``payload`` as JSON with sorted keys, so equal content gives equal bytes."""
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def sha256_of(path):
    """Hex sha256 of a file, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def installed_versions():
    """Versions of Python and of the distributions the fit depends on."""
    from importlib.metadata import PackageNotFoundError, version

    versions = {"python": platform.python_version()}
    for name in _VERSIONED_DISTRIBUTIONS:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


# ------------------------------------------------------------- MS2LDA-bound steps
def _ms2lda_defaults():
    """MS2LDA's CLI defaults and the folder its Spec2Vec download lands in."""
    import MS2LDA
    from scripts.ms2lda_runfull import DEFAULT_PARAMS

    spec2vec_dir = Path(MS2LDA.__file__).resolve().parent / "Add_On" / "Spec2Vec" / "model_positive_mode"
    return DEFAULT_PARAMS, spec2vec_dir


def check(request):
    """Problems that would stop the fit, found without fitting anything."""
    problems = []
    versions = installed_versions()
    if versions["ms2lda"] != REQUIRED_MS2LDA_VERSION:
        problems.append(
            f"ms2lda {versions['ms2lda']} is installed; this driver calls the internal "
            f"functions of ms2lda {REQUIRED_MS2LDA_VERSION}."
        )
        return problems, versions
    default_params, spec2vec_dir = _ms2lda_defaults()
    params = merged_parameters(default_params, request, spec2vec_dir)
    if request["mag"]:
        missing = missing_spec2vec_files(params["annotation_parameters"])
        if missing:
            problems.append(
                "MAG needs the Spec2Vec model, embeddings and spectra database; missing: "
                + ", ".join(missing)
                + ". Run `ms2lda --only-download` in the MS2LDA environment, or fit with --no-mag."
            )
    return problems, versions


def _inchikey_of(smiles):
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles) if smiles else None
    return Chem.MolToInchiKey(mol) if mol is not None else None


def _store_model_files(trained, motif_spectra, optimized_motifs, folder):
    """Write the trained model and the motifs in MS2LDA's own formats.

    Writes ``ms2lda.bin``, the ``.m2m`` motif files and ``motifset.json`` (MotifDB
    JSON), plus ``motifset_optimized.json`` when MAG produced optimised motifs, with
    the MS2LDA functions ``store_results`` uses and under the names it gives them.
    """
    from MS2LDA.Add_On.MassQL.MassQL4MotifDB import motifs2motifDB, store_motifDB
    from MS2LDA.motif_parser import store_m2m_folder

    folder.mkdir()
    store_m2m_folder(motif_spectra, str(folder / "motifs"))
    trained.save(str(folder / "ms2lda.bin"))
    ms1_motifdb, ms2_motifdb = motifs2motifDB(motif_spectra)
    store_motifDB(ms1_motifdb, ms2_motifdb, name=str(folder / "motifset.json"))
    if optimized_motifs is not None:
        ms1_motifdb, ms2_motifdb = motifs2motifDB(optimized_motifs)
        store_motifDB(ms1_motifdb, ms2_motifdb, name=str(folder / "motifset_optimized.json"))


def _store_without_mag(trained, motif_spectra, convergence_curve, doc2spec_map, folder):
    """Write MS2LDA's native output files for a fit without MAG.

    Writes the files of :func:`_store_model_files`, the convergence plot and the
    document map. The motif network and per-motif figures that ``store_results``
    draws from MAG's optimised motifs and candidate SMILES are not written.
    """
    import pickle

    import matplotlib.pyplot as plt
    from MS2LDA.Visualisation.visualisation import plot_convergence

    _store_model_files(trained, motif_spectra, None, folder)
    figure = plot_convergence(convergence_curve)
    figure.savefig(folder / "convergence_curve.png", dpi=300, bbox_inches="tight", pad_inches=0.2)
    plt.close(figure)
    with open(folder / "doc2spec_map.pkl", "wb") as handle:
        pickle.dump(doc2spec_map, handle)
    return str(folder)


def fit(request, versions):
    """Run the fit and write every output file except ``manifest.json``.

    Returns the manifest content.
    """
    import pandas as pd
    from MS2LDA.Add_On.Spec2Vec.annotation_refined import hit_clustering, motif_optimization
    from MS2LDA.modeling import create_motif_spectra, define_model, extract_motifs, train_model
    from MS2LDA.Preprocessing.generate_corpus import features_to_words, map_doc2spec
    from MS2LDA.Preprocessing.load_and_clean import clean_spectra
    from MS2LDA.run import add_annotation, filetype_check, s2v_annotation, store_results
    from MS2LDA.Visualisation.ldadict import (
        generate_corpusjson_from_tomotopy,
        save_visualization_data,
    )

    out_dir = Path(request["out_dir"])
    default_params, spec2vec_dir = _ms2lda_defaults()
    params = merged_parameters(default_params, request, spec2vec_dir)
    dataset = params["dataset_parameters"]
    n_motifs, n_iterations = request["n_motifs"], request["n_iterations"]
    timings = {}

    started = time.perf_counter()
    spectra, experiments = [], []
    for experiment in request["experiments"]:
        path = experiment["spectra_path"]
        loaded = list(filetype_check(path))
        for spectrum in loaded:
            spectrum.set("enpkg_run_name", experiment["run_name"])
        spectra.extend(loaded)
        experiments.append({
            "run_name": experiment["run_name"],
            "spectra_path": path,
            "sha256": sha256_of(path),
            "n_spectra_loaded": len(loaded),
        })
        logger.info("Loaded %d spectra for %s", len(loaded), experiment["run_name"])
    timings["load"] = time.perf_counter() - started

    started = time.perf_counter()
    cleaned = clean_spectra(spectra, params["preprocessing_parameters"])
    check_spectrum_ids(cleaned)
    feature_words = features_to_words(
        spectra=cleaned,
        significant_figures=dataset["significant_digits"],
        acquisition_type=dataset["acquisition_type"],
    )
    timings["clean_and_words"] = time.perf_counter() - started
    logger.info("%d of %d spectra kept by MS2LDA's cleaning", len(cleaned), len(spectra))

    started = time.perf_counter()
    model = define_model(n_motifs=n_motifs, model_parameters=params["model_parameters"])
    trained, convergence_curve = train_model(
        model,
        feature_words,
        iterations=n_iterations,
        train_parameters=params["train_parameters"],
        convergence_parameters=params["convergence_parameters"],
    )
    timings["train"] = time.perf_counter() - started
    if len(trained.docs) != len(cleaned):
        raise RuntimeError(
            f"The model holds {len(trained.docs)} documents for {len(cleaned)} cleaned spectra; "
            "documents cannot be linked to spectra by position."
        )

    motif_words = extract_motifs(trained, top_n=params["motif_parameter"])
    motif_spectra = create_motif_spectra(
        motif_words,
        charge=dataset["charge"],
        motifset_name=dataset["name"],
        significant_digits=dataset["significant_digits"],
    )

    optimized_motifs = clustered_smiles = clustered_scores = None
    if request["mag"]:
        started = time.perf_counter()
        annotation = params["annotation_parameters"]
        library_matches, s2v_similarity = s2v_annotation(motif_spectra, annotation)
        clustered_spectra, clustered_smiles, clustered_scores = hit_clustering(
            s2v_similarity=s2v_similarity,
            motif_spectra=motif_spectra,
            library_matches=library_matches,
            criterium=annotation["criterium"],
            cosine_similarity=annotation["cosine_similarity"],
        )
        motif_spectra = add_annotation(motif_spectra, clustered_smiles)
        optimized_motifs = motif_optimization(
            motif_spectra, clustered_spectra, clustered_smiles, loss_err=1
        )
        timings["mag"] = time.perf_counter() - started

    started = time.perf_counter()
    documents = document_rows(cleaned)
    tokens = [[trained.vocabs[word_id] for word_id in doc.words] for doc in trained.docs]
    lda_dict = generate_corpusjson_from_tomotopy(
        model=trained,
        documents=tokens,
        spectra=None,
        doc_metadata={f"spec_{i}": {"placeholder": f"Doc {i}"} for i in range(len(tokens))},
    )
    memberships = membership_rows(lda_dict, documents)
    del lda_dict, tokens
    timings["memberships"] = time.perf_counter() - started

    started = time.perf_counter()
    ms2lda_dir = Path(dataset["output_folder"])
    if not request["viz"]:
        _store_model_files(trained, motif_spectra, optimized_motifs, ms2lda_dir)
        timings["ms2lda_files"] = time.perf_counter() - started
    else:
        doc2spec_map = map_doc2spec(feature_words, cleaned)
        if request["mag"]:
            written = store_results(
                trained, motif_spectra, optimized_motifs, convergence_curve,
                clustered_smiles, doc2spec_map, str(ms2lda_dir),
            )
        else:
            written = _store_without_mag(trained, motif_spectra, convergence_curve, doc2spec_map, ms2lda_dir)
        timings["ms2lda_files"] = time.perf_counter() - started
        if n_motifs < VISUALIZATION_MAX_MOTIFS:
            started = time.perf_counter()
            save_visualization_data(
                trained, cleaned,
                optimized_motifs if optimized_motifs is not None else motif_spectra,
                doc2spec_map, written,
                run_parameters={"n_motifs": n_motifs, "n_iterations": n_iterations, **params},
            )
            timings["ms2lda_viz_file"] = time.perf_counter() - started

    write_json(out_dir / "motifs.json", {"motifs": motif_records(
        motif_words, optimized_motifs, clustered_smiles, clustered_scores, _inchikey_of,
    )})
    pd.DataFrame(documents, columns=["doc_id", "run_name", "feature_id", "precursor_mz"]).to_parquet(
        out_dir / "documents.parquet", index=False
    )
    pd.DataFrame(
        memberships,
        columns=["doc_id", "run_name", "feature_id", "motif", "probability", "overlap"],
    ).to_parquet(out_dir / "memberships.parquet", index=False)

    docs_per_run = {}
    for row in documents:
        docs_per_run[row["run_name"]] = docs_per_run.get(row["run_name"], 0) + 1
    for experiment in experiments:
        experiment["n_documents"] = docs_per_run.get(experiment["run_name"], 0)

    history = {name: [float(value) for value in values] for name, values in convergence_curve.items()}
    return {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ionization_mode": request["ionization_mode"],
        "n_motifs": n_motifs,
        "n_iterations": n_iterations,
        "mag": request["mag"],
        "viz": request["viz"],
        "software": versions,
        "parameters": params,
        "experiments": experiments,
        "corpus": {
            "n_spectra_loaded": len(spectra),
            "n_documents": len(cleaned),
            "n_documents_without_feature_id": sum(1 for row in documents if row["feature_id"] is None),
            "vocabulary_size": len(trained.used_vocabs),
            "n_memberships": len(memberships),
        },
        "convergence": {
            "iterations_run": len(history["perplexity_history"]) * params["convergence_parameters"]["step_size"],
            "history": history,
        },
        "timings_s": {name: round(seconds, 1) for name, seconds in timings.items()},
    }


# ------------------------------------------------------------------------ entry
def _configure_logging(log_file):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _load_tomotopy_first():
    """Import tomotopy before anything that imports pyarrow.

    pyarrow 17 (an ms2lda dependency, imported by pandas, matchms and MS2LDA) ships
    msvcp140.dll 14.28 in its package folder. On Windows the first copy of that DLL a
    process loads serves every later module, and tomotopy 0.14 dies with an access
    violation inside ``LDAModel.train`` under the 14.28 runtime. Imported first,
    tomotopy loads the environment's newer copy instead.
    """
    import tomotopy  # noqa: F401


def main(argv=None):
    _load_tomotopy_first()
    parser = argparse.ArgumentParser(description="Fit an MS2LDA 2.0 model for enpkg.")
    parser.add_argument("request", type=Path, help="request.json written by `enpkg motifs fit`")
    parser.add_argument("--check", action="store_true",
                        help="Only report versions and missing resources, as JSON on stdout.")
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))

    if args.check:
        problems, versions = check(request)
        print(json.dumps({"versions": versions, "problems": problems}))
        return 2 if problems else 0

    out_dir = Path(request["out_dir"])
    _configure_logging(out_dir / "fit.log")
    problems, versions = check(request)
    if problems:
        for problem in problems:
            logger.error(problem)
        return 2
    manifest = fit(request, versions)
    write_json(out_dir / "manifest.json", manifest)
    logger.info("Fit complete: %d motifs, %d documents, %d memberships",
                manifest["n_motifs"], manifest["corpus"]["n_documents"],
                manifest["corpus"]["n_memberships"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
