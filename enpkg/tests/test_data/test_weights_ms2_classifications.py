"""MS2 reweighting input is unaffected by annotations that carry no source organism.

A library-only MS2 annotation has no organism and zero-filled classification
vectors. ``WeightsEnhancer.compute_ms2_classifications`` filters on
``has_organisms()`` in three separate comprehensions that must stay aligned, so the
check is that interleaving such annotations leaves the feature matrices bit-identical.
"""

import logging

import numpy as np

from enpkg.monolith.configuration.MSEnhancer_config import GeneralParams
from enpkg.monolith.configuration.reweighting_config import ReweightingConfig
from enpkg.monolith.data.chemical_annotation import (
    AnnotationOrganism,
    LibraryStructure,
    MS2ChemicalAnnotation,
)
from enpkg.monolith.data.otl_class import LineageItem, Match, Taxon
from enpkg.monolith.enhancers.weights_enhancer import WeightsEnhancer

_N_PATHWAYS, _N_SUPERCLASSES, _N_CLASSES = 3, 4, 5


def _taxon(name: str, rank: str, ott_id: int) -> Taxon:
    return Taxon(
        flags=[], is_suppressed=False, is_suppressed_from_synth=False, name=name,
        open_tree_taxon_id=ott_id, rank=rank, source="ott", synonyms=[], tax_sources=[],
        unique_name=name,
    )


def _artemisia_annua_match() -> Match:
    match = Match(
        is_approximate_match=False, is_synonym=False, matched_name="Artemisia annua",
        nomenclature_code="ICN", score=1.0, search_string="Artemisia annua",
        taxon=_taxon("Artemisia annua", "species", 1),
    )
    match.set_lineage(LineageItem(
        flags=[], is_suppressed=False, is_suppressed_from_synth=False,
        taxa=[_taxon("Artemisia", "genus", 2), _taxon("Asteraceae", "family", 3)],
    ))
    return match


def _organism(species: str, genus: str, family: str) -> AnnotationOrganism:
    return AnnotationOrganism(
        name=species, wikidata=None, ott_id=None, domain="Eukaryota", kingdom="Plantae",
        phylum=None, klass=None, order=None, family=family, genus=genus, species=species,
    )


def _lotus_backed(score: float, organism: AnnotationOrganism, seed: int) -> MS2ChemicalAnnotation:
    rng = np.random.default_rng(seed)
    return MS2ChemicalAnnotation(
        source="Lotus",
        short_inchikey=f"LOTUSBACKED{seed:03d}",
        score=score,
        algorithm="cosine_greedy",
        pathway_scores=rng.random(_N_PATHWAYS),
        superclass_scores=rng.random(_N_SUPERCLASSES),
        class_scores=rng.random(_N_CLASSES),
        organisms=[organism],
    )


def _library_only(score: float) -> MS2ChemicalAnnotation:
    return MS2ChemicalAnnotation(
        source="LIB:1.0",
        short_inchikey="LIBRARYONLYIKX",
        score=score,
        algorithm="cosine_greedy",
        pathway_scores=np.zeros(_N_PATHWAYS, dtype=np.float32),
        superclass_scores=np.zeros(_N_SUPERCLASSES, dtype=np.float32),
        class_scores=np.zeros(_N_CLASSES, dtype=np.float32),
        library_structure=LibraryStructure(
            inchikey="LIBRARYONLYIKX-UHFFFAOYSA-N", inchi=None, smiles=None,
            molecular_formula=None, compound_name=None, npc_pathway=None,
            npc_superclass=None, npc_class=None, classyfire_superclass=None,
            classyfire_class=None, classyfire_subclass=None,
        ),
    )


def _weights_enhancer() -> WeightsEnhancer:
    enhancer = WeightsEnhancer(
        ReweightingConfig(general_params=GeneralParams(recompute=False, ionization_mode="pos")),
        logging.getLogger(__name__),
        lotus_store=None,
    )
    # enhance() reads these from the LotusStore before computing; set them directly.
    enhancer._number_of_pathways = _N_PATHWAYS
    enhancer._number_of_superclasses = _N_SUPERCLASSES
    enhancer._number_of_classes = _N_CLASSES
    return enhancer


def test_library_only_annotations_leave_the_ms2_feature_matrices_unchanged(make_analysis):
    analysis = make_analysis(n_spectra=1)
    analysis = analysis.model_copy(update={"ott_matches": [_artemisia_annua_match()]})
    spectrum = analysis.spectra[0]
    same_species = _lotus_backed(0.8, _organism("Artemisia annua", "Artemisia", "Asteraceae"), 1)
    same_family = _lotus_backed(0.6, _organism("Bellis perennis", "Bellis", "Asteraceae"), 2)
    enhancer = _weights_enhancer()

    spectrum.ms2_annotations = [same_species, same_family]
    without = enhancer.compute_ms2_classifications(analysis)

    # Interleaved, with high cosines: a misaligned filter would pair a LOTUS-backed
    # annotation's vectors with a library-only annotation's weight.
    spectrum.ms2_annotations = [_library_only(0.99), same_species, _library_only(0.95), same_family]
    with_library_only = enhancer.compute_ms2_classifications(analysis)

    # The taxonomical weights differ between the two LOTUS-backed annotations, so a
    # mix-up would move these matrices rather than cancel out.
    assert not np.allclose(without[0], same_species.pathway_scores)
    for before, after in zip(without, with_library_only, strict=True):
        assert np.array_equal(before, after)
