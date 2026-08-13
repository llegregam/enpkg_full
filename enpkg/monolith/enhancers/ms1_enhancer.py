"""MS1 enhancer: attaches candidate adducts to each spectrum by precursor mass.

For every spectrum it collects the reference adducts (LOTUS structures under
every ionization recipe) whose computed ion mass falls within ``parent_mz_tol``
of the precursor m/z, via a binary search over the mass-sorted adduct list. See
``docs/MS1_ENHANCER.md`` for the conceptual walkthrough.
"""

import random
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from logging import Logger
from time import time

from tqdm.auto import tqdm

from enpkg.monolith.configuration.MSEnhancer_config import MSEnhancerConfig
from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.lotus_class import Lotus
from enpkg.monolith.data.ms1_data_classes import ChemicalAdduct
from enpkg.monolith.data.ms1_data_classes.adduct_class import ADDUCT_MASSES
from enpkg.monolith.enhancers.adducts import NEGATIVE_RECIPES, POSITIVE_RECIPES
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.loaders.lotus_store import LotusStore
from enpkg.monolith.utils.ms1_cluster_dispatch import inherit_satellite_annotations


class MS1Enhancer(Enhancer):
    """Enhancer that adds MS1 adduct annotations to the analysis."""

    def __init__(self, configuration: MSEnhancerConfig, logger: Logger, lotus_store: LotusStore):
        """Initializes the enhancer."""

        if not isinstance(configuration, MSEnhancerConfig):
            raise TypeError(
                f"Expected configuration of type MSEnhancerConfig, got {type(configuration)}"
            )
        if not isinstance(logger, Logger):
            raise TypeError(f"Expected logger of type logging.Logger, got {type(logger)}")
        if not isinstance(lotus_store, LotusStore):
            raise TypeError(f"Expected lotus_store of type LotusStore, got {type(lotus_store)}")

        self.configuration = configuration
        self.logger = logger
        # LotusStore owns compound access; MS1 doesn't touch the spectral library.
        self.lotus_store = lotus_store

    def initialize_adducts(
        self, lotus_grouped_by_structure_molecular_formula: list[list[Lotus]]
    ) -> list[ChemicalAdduct]:
        """Build a mass-sorted list of ChemicalAdduct objects.

        One ChemicalAdduct is created per (LOTUS formula group, recipe) pair for
        the configured ionization mode, then the list is sorted by adduct mass so
        precursor matching in ``enhance`` can binary-search it.
        """
        match self.configuration.general_params.ionization_mode:
            case "pos":
                self.logger.info("Initializing positive adducts")
                recipes = POSITIVE_RECIPES
            case "neg":
                self.logger.info("Initializing negative adducts")
                recipes = NEGATIVE_RECIPES
            case _:
                raise ValueError(
                    f"Invalid ionization mode {self.configuration.general_params.ionization_mode!r}, "
                    "expected 'pos' or 'neg'"
                )

        adducts: list[ChemicalAdduct] = [
            ChemicalAdduct(lotus=lotus_group, recipe=recipe)
            for lotus_group in lotus_grouped_by_structure_molecular_formula
            for recipe in recipes
        ]

        adducts.sort(key=lambda x: x.adduct_mass)
        self.logger.debug(
            "Sample adducts (first 10): %s",
            "\n".join(
                f"  {i+1}. {adduct}"
                for i, adduct in enumerate(random.sample(adducts, min(10, len(adducts))))
            ),
        )
        return adducts

    def initialize_lotus_objects(self, spectrum_list: Sequence) -> list[list[Lotus]]:
        """Return LOTUS entries reachable from the spectra, grouped by formula.

        Computes the exact-mass window reachable from the spectra's precursor
        m/z values (across every ionization recipe) and delegates the grouped
        Lotus build to the LotusStore.

        Parameters
        ----------
        spectrum_list:
            AnnotatedSpectrum objects whose precursor m/z values drive the
            mass-window filter.

        Returns
        -------
        List of lists of LOTUS objects, each inner list sharing a molecular
        formula.
        """
        match self.configuration.general_params.ionization_mode:
            case "pos":
                recipes = POSITIVE_RECIPES
            case "neg":
                recipes = NEGATIVE_RECIPES
            case _:
                raise ValueError(
                    f"Invalid ionization mode {self.configuration.general_params.ionization_mode!r}, "
                    "expected 'pos' or 'neg'"
                )
        tol = self.configuration.spectral_match_params.parent_mz_tol
        precursor_mzs = [s.precursor_mz for s in spectrum_list]

        # Widest exact-mass window reachable from all spectra × all recipes.
        # For each recipe: exact_mass = (precursor_mz * charge - adduct_sum) / multimer_factor
        global_min = float("inf")
        global_max = float("-inf")
        for recipe in recipes:
            adduct_sum = sum(ADDUCT_MASSES[k] * v for k, v in recipe.ingredients.items())
            for mz in precursor_mzs:
                em_min = ((mz - tol) * recipe.charge - adduct_sum) / recipe.multimer_factor
                em_max = ((mz + tol) * recipe.charge - adduct_sum) / recipe.multimer_factor
                global_min = min(global_min, em_min)
                global_max = max(global_max, em_max)

        self.logger.debug(
            f"Mass-window query: exact_mass ∈ [{global_min:.4f}, {global_max:.4f}] "
            f"Da (from {len(precursor_mzs)} spectra × {len(recipes)} recipes)"
        )
        return self.lotus_store.grouped_by_formula_for_mass_range(global_min, global_max)

    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "MS1 Enhancer"

    def enhance(self, analysis: Analysis) -> Analysis:
        """Attach candidate MS1 adducts to each spectrum of the analysis.

        Spectra are mutated in place (``spectrum.ms1_annotations``) and the same
        ``Analysis`` is returned — the uniform enhancer contract.
        """
        spectrum_list = analysis.spectra
        number_of_spectra = len(spectrum_list)
        self.logger.info("Running MS1 enrichment on %d spectra", number_of_spectra)

        # Cluster-aware dispatch: if the MS1 graph enhancer ran (roles are stamped),
        # only anchors + singletons get the LOTUS mass-search; satellites inherit
        # their anchor's resolved molecule afterwards (no redundant search). With no
        # roles stamped, every feature is searched (unchanged behaviour).
        graph_ran = any(s.ms1_cluster_role is not None for s in spectrum_list)
        search_spectra = (
            [s for s in spectrum_list if s.ms1_cluster_role != "satellite"]
            if graph_ran
            else list(spectrum_list)
        )
        if graph_ran:
            self.logger.info(
                "MS1 graph roles present: searching %d anchors/singletons, "
                "%d satellites will inherit their anchor's molecule",
                len(search_spectra),
                number_of_spectra - len(search_spectra),
            )

        # Adducts are rebuilt on every enhance() call
        self.logger.info("Initializing LOTUS objects and adducts")
        start = time()
        lotus_grouped_by_formula = self.initialize_lotus_objects(spectrum_list=search_spectra)
        self.logger.info("Initialized LOTUS objects in %.2f seconds", time() - start)
        start = time()
        self._adducts = self.initialize_adducts(lotus_grouped_by_formula)
        self.logger.info(
            "Initialized %d adducts in %.2f seconds", len(self._adducts), time() - start
        )

        # Pre-extract the sorted mass key once so each spectrum is two bisects.
        adduct_masses = [adduct.adduct_mass for adduct in self._adducts]
        tol = self.configuration.spectral_match_params.parent_mz_tol

        for spectrum in tqdm(
            search_spectra,
            leave=False,
            total=len(search_spectra),
            desc="Filtering precursor adducts",
            dynamic_ncols=True,
        ):
            # Adducts whose mass lies in [precursor - tol, precursor + tol].
            # bisect is inclusive-safe even when the precursor is heavier than
            # every adduct (left == right == len -> empty slice), unlike the old
            # linear upper-bound scan which indexed before checking bounds.
            lower = spectrum.precursor_mz - tol
            upper = spectrum.precursor_mz + tol
            left = bisect_left(adduct_masses, lower)
            right = bisect_right(adduct_masses, upper)
            spectrum.ms1_annotations = self._adducts[left:right]

        # Satellites inherit their anchor's resolved molecule (re-cast under their
        # own adduct form) rather than a redundant precursor-mass search.
        if graph_ran:
            inherit_satellite_annotations(spectrum_list, self.logger)

        return analysis
