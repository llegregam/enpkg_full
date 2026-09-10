"""Smoke test: serialize real enriched `Analysis` pickles to Turtle for GraphDB.

Loads ``<batch-dir>/*/analysis.pkl`` (as written by the batch runner), serializes
each to ``<out-dir>/<run_name>.ttl`` and runs sanity checks, persisting the
``.ttl`` files for inspection in a GraphDB instance.

Prerequisite: the pickles must come from the current code — re-run a batch after
the MS2 refactor, otherwise the stale `MS2ChemicalAnnotation` shape makes the MS2
path raise (the script reports that per-analysis rather than crashing).

Usage::

    python -m enpkg.monolith.rdf.smoke_serialize [--batch-dir DIR] [--out-dir DIR]
        [--no-fbmn-components]
        [--include-ions] [--min-relative-intensity F] [--max-ions-per-spectrum N]
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path
from typing import Optional

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF

from enpkg.monolith.configuration.serializer_config import SerializerConfig

from .namespaces import EMI, ENPKG
from .serializer import AnalysisSerializer

# Node types reported per analysis.
_TYPES = {
    "LCMSAnalysis": EMI.LCMSAnalysis,
    "LCMSFeature": EMI.LCMSFeature,
    "StructuralAnnotation": EMI.StructuralAnnotation,
    "AdductAnnotation": ENPKG.AdductAnnotation,
    "SpectralAnnotation": ENPKG.SpectralAnnotation,
    "SiriusAnnotation": ENPKG.SiriusAnnotation,
    "ChemicalStructure": EMI.ChemicalStructure,
    "Taxon": EMI.Taxon,
    "LFpair": EMI.LFpair,
    "FBMNComponent": EMI.FBMNComponent,
    "AdductRecipe": ENPKG.AdductRecipe,
    "AdductCluster": ENPKG.AdductCluster,
    "InChIKey2D": EMI.InChIKey2D,
}
_JUNK_LITERALS = {"nan", "none", ""}
# Characters illegal anywhere in an IRI (whitespace + RFC-3986 forbidden set).
# Note: '#' is omitted because EMI vocabulary terms legitimately use it as the
# namespace separator (e.g. emi#LCMSAnalysis).
_ILLEGAL_URI_CHARS = (" ", "\t", "\n", "\r", "<", ">", '"', "`", "{", "}", "|", "\\", "^")


def _find_latest_batch() -> Optional[Path]:
    batches = sorted(Path("gui_workspace/logs").glob("batch_*"))
    return batches[-1] if batches else None


def _check(mem_graph: Graph, file_graph: Graph, analysis) -> tuple[bool, dict]:
    """Run hard checks on a serialized analysis; return (all_passed, {name: (ok, detail)})."""
    results: dict[str, tuple[bool, str]] = {}

    results["round_trip"] = (
        len(mem_graph) == len(file_graph),
        f"{len(mem_graph)} -> {len(file_graph)}",
    )

    n_analysis = len(set(file_graph.subjects(RDF.type, EMI.LCMSAnalysis)))
    results["analysis_node"] = (n_analysis == 1, str(n_analysis))

    n_features = len(set(file_graph.subjects(RDF.type, EMI.LCMSFeature)))
    results["features==spectra"] = (
        n_features == analysis.number_of_spectra,
        f"{n_features} vs {analysis.number_of_spectra}",
    )

    bad_uris = sorted(
        {str(term) for triple in file_graph for term in triple
         if isinstance(term, URIRef) and any(ch in str(term) for ch in _ILLEGAL_URI_CHARS)}
    )
    results["uri_hygiene"] = (not bad_uris, f"{len(bad_uris)} bad{(': ' + bad_uris[0]) if bad_uris else ''}")

    bad_literals = sorted(
        {str(o) for _, _, o in file_graph
         if isinstance(o, Literal) and str(o).strip().lower() in _JUNK_LITERALS}
    )
    results["literal_hygiene"] = (not bad_literals, f"{len(bad_literals)} junk")

    # Nothing in the serializer mints a BNode any more (LFpairs were the last ones),
    # so any blank node here is a regression — they are invisible in GraphDB's
    # browser and are re-minted on every serialization, breaking idempotency.
    blank = {term for triple in file_graph for term in triple if isinstance(term, BNode)}
    results["no_blank_nodes"] = (not blank, str(len(blank)))

    return all(ok for ok, _ in results.values()), results


def _info(graph: Graph) -> dict:
    """Soft, reported-only metrics."""
    type_counts = {name: len(set(graph.subjects(RDF.type, t))) for name, t in _TYPES.items()}
    distinct_compounds = type_counts["ChemicalStructure"]
    total_refs = sum(1 for _ in graph.triples((None, EMI.hasChemicalStructure, None)))
    annotated = graph.query(
        "SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE "
        "{ ?f a ?ft ; ?has ?a . ?a ?hcs ?c }",
        initBindings={"ft": EMI.LCMSFeature, "has": EMI.hasAnnotation, "hcs": EMI.hasChemicalStructure},
    )
    annotated_structures = int(next(iter(annotated))[0]) if len(annotated) else 0
    # MS2 matches coupled to a corresponding MS1 adduct (D2).
    coupled_ms2 = len(set(graph.subjects(ENPKG.hasCorrespondingAdduct, None)))
    return {
        "types": type_counts,
        "compound_sharing": f"{distinct_compounds} distinct / {total_refs} refs",
        "annotated_structures": annotated_structures,
        "ms2_with_corresponding_adduct": coupled_ms2,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serialize enriched analyses to Turtle (smoke test).")
    parser.add_argument("--batch-dir", type=Path, default=None,
                        help="Dir containing <run>/analysis.pkl (default: newest gui_workspace/logs/batch_*).")
    parser.add_argument("--out-dir", type=Path, default=Path("gui_workspace/rdf_out"))
    parser.add_argument("--top-k-ms1", type=int, default=5,
                        help="Keep only the top-k MS1 annotations per spectrum (by reweighted score). "
                             "Default 5; pass <=0 to disable the cap (emit all).")
    parser.add_argument("--top-k-ms2", type=int, default=5,
                        help="Keep only the top-k MS2 annotations per spectrum (by reweighted score, "
                             "else cosine). Default 5; pass <=0 to disable the cap (emit all).")
    parser.add_argument("--top-k-sirius", type=int, default=None,
                        help="Keep only the top-k SIRIUS annotations per spectrum (by structurePerIdRank). "
                             "Default: emit all (the summary file is already SIRIUS's top-X).")
    parser.add_argument("--no-fbmn-components", dest="include_fbmn_components",
                        action="store_false",
                        help="Skip the emi:FBMNComponent nodes for the network's connected "
                             "components. On by default (O(features), unlike the quadratic "
                             "LFpair edges); a no-op when the analysis carries no network.")
    parser.add_argument("--include-ions", action="store_true")
    parser.add_argument("--min-relative-intensity", type=float, default=0.0)
    parser.add_argument("--max-ions-per-spectrum", type=int, default=None)
    args = parser.parse_args(argv)

    batch_dir = args.batch_dir or _find_latest_batch()
    if batch_dir is None or not batch_dir.exists():
        parser.error(f"No batch dir found (looked for {batch_dir or 'gui_workspace/logs/batch_*'}).")
    pkls = sorted(batch_dir.glob("*/analysis.pkl"))
    if not pkls:
        parser.error(f"No analysis.pkl under {batch_dir}/*/")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    # Same model the runners and the GUI use, so a flag combination that is rejected
    # here is rejected there too.
    opts = SerializerConfig(
        top_k_ms1=args.top_k_ms1,
        top_k_ms2=args.top_k_ms2,
        top_k_sirius=args.top_k_sirius,
        include_fbmn_components=args.include_fbmn_components,
        include_ions=args.include_ions,
        min_relative_intensity=args.min_relative_intensity,
        max_ions_per_spectrum=args.max_ions_per_spectrum,
    ).model_dump()

    print(f"Batch:  {batch_dir}")
    print(f"Output: {args.out_dir.resolve()}")
    print(f"Top-k:  ms1={args.top_k_ms1}  ms2={args.top_k_ms2}  sirius={args.top_k_sirius}")
    print(f"Network: components={'on' if args.include_fbmn_components else 'off'}")
    print(f"Ions:   {'on' if args.include_ions else 'off'}\n")

    overall_ok = True
    for pkl in pkls:
        label = pkl.parent.name
        try:
            analysis = pickle.loads(pkl.read_bytes())
        except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
            print(f"[LOAD FAIL] {label}: {type(exc).__name__}: {exc}")
            overall_ok = False
            continue

        ttl_path = args.out_dir / f"{analysis.run_name}.ttl"
        try:
            serializer = AnalysisSerializer(**opts)
            serializer.add_analysis(analysis)
            serializer.graph.serialize(destination=str(ttl_path), format="turtle")
            file_graph = Graph()
            file_graph.parse(str(ttl_path), format="turtle")
        except Exception as exc:  # noqa: BLE001
            print(f"[SER FAIL]  {analysis.run_name}: {type(exc).__name__}: {exc}")
            overall_ok = False
            continue

        ok, results = _check(serializer.graph, file_graph, analysis)
        info = _info(file_graph)
        overall_ok = overall_ok and ok
        print(f"[{'OK  ' if ok else 'FAIL'}] {analysis.run_name}: "
              f"{len(serializer.graph)} triples, {analysis.number_of_spectra} spectra | "
              f"{info['compound_sharing']} compounds, {info['annotated_structures']} annotated -> {ttl_path.name}")
        if not ok:
            for name, (passed, detail) in results.items():
                if not passed:
                    print(f"          - {name}: {detail}")

    print("\n" + ("ALL OK" if overall_ok else "SOME CHECKS FAILED"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
