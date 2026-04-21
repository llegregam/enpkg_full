"""
Code for the Result exporter. 
Digests an enhanced result and produces different export interfaces.
"""


from enpkg.monolith.gui.runner import RunResult


def quicksort(annotation_list):
    if len(annotation_list) <= 1:
        return annotation_list
    pivot = annotation_list[0]
    left = [x for x in annotation_list if x.scores["cosine_hungarian"]["value"] < pivot]
    middle = [x for x in annotation_list if x.scores["cosine_hungarian"]["value"] == pivot]
    right = [x for x in annotation_list if x.scores["cosine_hungarian"]["value"] > pivot]
    return quicksort(left) + middle + quicksort(right)

class ResultExporter:
    """
    Exports an enhanced analysis in different formats.
    """

    @classmethod
    def ingest_result(cls, run_result: RunResult) -> "ResultExporter":
        """
        Ingest the enhanced result to be exported

        Args:
            enhanced_result: The enhanced result to be exported
        Returns:
            An instance of the ResultExporter class with the ingested result, ready for serialization.
        """
        for spectrum in run_result.analysis.spectra:
            print(f" - Spectrum {spectrum.feature_id} with {len(spectrum.peaks)} peaks")
            print(f"   - MS1 annotations: {len(spectrum.ms1_annotations)}")
            print(f"   - MS2 annotations: {len(spectrum.ms2_annotations)}")
            for annotation in spectrum.ms2_annotations:
                print(f"Spectrum {spectrum.feature_id} - MS2 annotation: {annotation}")

if __name__ == "__main__":
    import pickle
    import queue
    from pathlib import Path

    from enpkg.monolith.configuration.MSEnhancer_config import (
        DownloaderParams,
        GeneralParams,
        MSEnhancerConfig,
        Paths,
        SpectralMatchParams,
        Urls,
    )
    from enpkg.monolith.configuration.network_enhancer_config import NetworkEnhancerConfig
    from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
    from enpkg.monolith.gui.runner import run_pipeline

    TEST_DATA = Path("/home/llegregam/git_projects/enpkg_full/gui_workspace/input/")
    CACHE = Path(__file__).resolve().parent / "cache" / "last_run.pkl"
    DATABASE_DIR = Path("/home/llegregam/git_projects/enpkg_full/gui_workspace/databases/")

    if CACHE.exists():
        print(f"Loading cached RunResult from {CACHE}")
        result = pickle.loads(CACHE.read_bytes())
    else:
        print("No cache found — running the pipeline (this will take a while).")
        urls = Urls(
            taxo_db_metadata="https://zenodo.org/record/7534071/files/230106_frozen_metadata.csv.gz",
            taxo_db_pathways="https://zenodo.org/records/13951644/files/pathways.csv.gz?download=1",
            taxo_db_superclasses="https://zenodo.org/records/13951644/files/superclasses.csv.gz?download=1",
            taxo_db_classes="https://zenodo.org/records/13951644/files/classes.csv.gz?download=1",
            spectral_db_pos="https://zenodo.org/records/8287341/files/isdb_pos_cleaned.pkl",
        )
        ms_config = MSEnhancerConfig(
            downloader_params=DownloaderParams(
                redownload_if_exists=False,
                download_dir=str(DATABASE_DIR),
                urls=urls,
                paths=Paths(),
                duckdb_path=str(DATABASE_DIR / "enpkg.duckdb"),
            ),
            spectral_match_params=SpectralMatchParams(
                parent_mz_tol=0.01,
                method="cosine_hungarian",
                msms_mz_tol=0.01,
                min_score=0.20,
                min_peaks=12,
            ),
            general_params=GeneralParams(recompute=False, polarity="pos"),
        )
        reweighting_config = ReweightingConfig(
            downloader_params=ms_config.downloader_params,
            general_params=ms_config.general_params,
        )

        selected = ["taxonomical", "network", "ms1", "ms2", "weights"]
        configs = {
            "network": NetworkEnhancerConfig(),
            "ms1": ms_config,
            "ms2": ms_config,
            "weights": reweighting_config,
        }

        result = run_pipeline(
            selected_ids=selected,
            configs=configs,
            spectra_path=TEST_DATA / "actea_EtOAc-1_pos.mgf",
            metadata_path=TEST_DATA / "qualome_metadata.txt",
            quant_path=TEST_DATA / "actea_EtOAc-1_pos_quant.csv",
            ionization_mode="pos",
            database_dir=DATABASE_DIR,
            log_queue=queue.Queue(),
            verbose=True,
        )
        if result.error:
            raise RuntimeError(f"Pipeline failed: {result.error}")

        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(pickle.dumps(result))
        print(f"Cached RunResult to {CACHE}")

    exporter = ResultExporter.ingest_result(result)
    print(f"Ingested {exporter}")
