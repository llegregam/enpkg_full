import hashlib
from typing import Optional

from rdflib import URIRef

from .namespaces import EMI_RES, INCHIKEY
from ..data.analysis import Analysis
from ..data.annotated_spectra_class import AnnotatedSpectrum
from ..data.lotus_class import Lotus
from ..data.ms1_data_classes.adduct_class import ChemicalAdduct, AdductRecipe
from ..data.chemical_annotation import MS2ChemicalAnnotation, AnnotationOrganism
from ..data.otl_class import Match


class AnalysisURIs:
    """Stable URIs for entities related to an Analysis.
    These are minted in the context of a specific Analysis, 
    so they can use run-scoped identifiers (e.g. feature_id) and best-match taxon IDs.
    """
    @staticmethod
    def analysis_uri(analysis: Analysis) -> URIRef:
        """Mint the URI for the Analysis itself.

        Single stable key: the run name uniquely identifies a run.
        """
        return EMI_RES[f"analysis/{analysis.run_name}"]

    @staticmethod
    def featureset_uri(analysis: Analysis) -> URIRef:
        """Mint the URI for the analysis's LCMS feature set.

        EMI nests spectra under an intermediate node:
        Analysis -hasLCMSFeatureSet-> LCMSFeatureSet -hasLCMSFeature-> LCMSFeature.
        One feature set per run.
        """
        return EMI_RES[f"featureset/{analysis.run_name}"]

    # SPECTRUM URIs
    @staticmethod
    def spectrum_uri(analysis: Analysis, spectrum: AnnotatedSpectrum) -> URIRef:
        """Mint the URI for one spectrum within an analysis.

        A feature_id is only unique *within* a run, so the URI is a composite
        of the parent run_name and the feature_id. This is the general pattern
        for any child entity that lacks a globally stable identifier.
        """
        return EMI_RES[f"spectrum/{analysis.run_name}/{spectrum.feature_id}"]

    # MS1 ADDUCT URIs
    @staticmethod
    def chemical_adduct_uri(
        analysis: Analysis, spectrum: AnnotatedSpectrum, adduct: ChemicalAdduct
    ) -> URIRef:
        """Mint the URI for one MS1 adduct hypothesis on a spectrum.

        Run-scoped: an adduct only exists for a given spectrum in a given run.
        A spectrum carries several competing hypotheses (spectrum.ms1_annotations),
        so the recipe hash discriminates them. Never str(adduct) — that's an
        unstable Pydantic repr, not a stable key.
        """
        return EMI_RES[
            f"adduct/{analysis.run_name}/{spectrum.feature_id}/{_recipe_hash(adduct.recipe)}"
        ]

    @staticmethod
    def recipe_uri(recipe: AdductRecipe) -> URIRef:
        """Mint the URI for an adduct recipe (globally shared).

        No run/spectrum context: the same recipe — e.g. [M+H]+ — is one node
        reused by every adduct that applies it, so it is keyed only on the
        recipe definition. (Kept here for proximity to the adduct; its identity
        is global, not analysis-scoped.)
        """
        return EMI_RES[f"recipe/{_recipe_hash(recipe)}"]

    # MS2 ANNOTATION URIs
    @staticmethod
    def ms2_annotation_uri(
        analysis: Analysis,
        spectrum: AnnotatedSpectrum,
        annotation: MS2ChemicalAnnotation,
    ) -> URIRef:
        """Mint the URI for one MS2 spectral-library annotation on a spectrum.

        Run-scoped and per-spectrum: this is a reified *match event* carrying
        spectrum-specific evidence (score, n_matched_peaks), so it is never
        shared between spectra — run_name + feature_id keep each measurement
        distinct (even across analyses). Sharing happens one level down, at the
        compound (InChIKey) and organism nodes this annotation points to. Keyed
        on the library queried + the matched structure (short InChIKey) — the
        stable identity of the match, not a positional index. queried_against is
        Optional, so a sentinel keeps the path shape uniform.
        """
        queried_against = annotation.queried_against or "unspecified"
        return EMI_RES[
            f"ms2ann/{analysis.run_name}/{spectrum.feature_id}/"
            f"{queried_against}/{annotation.short_inchikey}"
        ]

    @staticmethod
    def analysis_metadata_uri(analysis: Analysis) -> URIRef:
        """Mint the URI for the metadata of an analysis.

        This is a separate entity from the analysis itself, since it has a
        different set of attributes and may be used in different contexts.
        """
        return EMI_RES[f"metadata/{analysis.run_name}"]
    
    @staticmethod
    def source_organism_uri(analysis: Analysis) -> Optional[URIRef]:
        """Mint the URI for the sample's source organism.

        Identity comes from the best OTT match, not the raw source_taxon string,
        and is keyed only on the taxon (never the run) so the same organism
        collapses to a single node across analyses:
          1. canonical Wikidata IRI, when the OTT->Wikidata lookup succeeded
          2. else a URI minted under EMI_RES, keyed on the stable OTT id
        The OTT id and source_taxon string stay as literal properties on the
        node (emitted by the serializer), not as its identity.
        """
        match = analysis.best_ott_matches
        if match is None:
            return None
        wikidata = match.taxon.wikidata.wd if match.taxon.wikidata is not None else None
        return _organism_uri(wikidata, match.open_tree_taxon_id)

    @staticmethod
    def ott_match_uri(analysis: Analysis, match: Match) -> URIRef:
        """Mint the URI for an OTT taxonomy-resolution event.

        Distinct from the organism it resolves to: this node carries the match
        quality (matched_name, score, is_approximate_match, is_synonym) as
        literals and links to the organism node. Run-scoped and keyed on the
        resolved taxon, so re-resolving the same taxon within a run is one node.
        """
        return EMI_RES[f"ottmatch/{analysis.run_name}/{match.open_tree_taxon_id}"]


def _short_hash(text: str) -> str:
    """Stable, process-independent short hash.

    Uses hashlib, NOT the builtin hash() (which is different per process via
    PYTHONHASHSEED), so the same input always yields the same digest across
    runs — required for reproducible URIs.
    """
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


def _recipe_hash(recipe: AdductRecipe) -> str:
    """Short, stable hash identifying an adduct recipe.

    Ingredient keys are sorted so two equivalent recipes (same ingredients in
    any dict order) collapse to the same hash.
    """
    ingredients = ",".join(f"{k}:{recipe.ingredients[k]}" for k in sorted(recipe.ingredients))
    return _short_hash(f"{recipe.charge}|{recipe.positive}|{recipe.multimer_factor}|{ingredients}")


def _organism_uri(wikidata: Optional[str], ott_id: Optional[int]) -> Optional[URIRef]:
    """Shared organism-identity cascade.

    Keyed only on stable identifiers so the same organism collapses to one node
    wherever it is referenced (sample source, compound provenance, ...):
      1. the full Wikidata IRI (LOTUS and the OTT->Wikidata query both store the IRI)
      2. else a URI minted under EMI_RES, keyed on the stable OTT id
      3. else None — no stable key, so no node; the name stays a literal.
    """
    if wikidata:
        return URIRef(wikidata)
    if ott_id is not None:
        return EMI_RES[f"taxon/ott/{ott_id}"]
    return None


class CompoundURIs:
    """
    Stable URIs for Compounds.
    These can be produced from any context.
    """
    @staticmethod
    def lotus_uri(lotus: Lotus) -> URIRef:
        """Mint the URI for a LOTUS compound (globally shared).

        Keyed on structural identity so the same compound collapses to one node
        wherever it is referenced (MS1 adducts, MS2 annotations, ...):
          1. InChIKey via identifiers.org (canonical, resolvable)
          2. else the structure's Wikidata IRI (LOTUS stores the full IRI)
          3. else a minted URI keyed on a stable hash of the SMILES
        """
        if lotus.structure_inchikey:
            return INCHIKEY[lotus.structure_inchikey]
        if lotus.structure_wikidata:
            return URIRef(lotus.structure_wikidata)
        # TODO: Discuss if this fallback is a good compromise, given that SMILES
        # can be non-canonical and may not be stable across LOTUS versions.
        return EMI_RES[f"compound/smiles/{_short_hash(lotus.structure_smiles)}"]


class OrganismURIs:
    """Stable URIs for source organisms (globally shared).

    An organism is one node wherever it is referenced — a compound's provenance,
    a sample's source, etc. — so identity is keyed only on stable identifiers,
    never on the analysis or the run.
    """

    @staticmethod
    def organism_uri(organism: AnnotationOrganism) -> Optional[URIRef]:
        """Mint the URI for a compound's source organism.

        Uses the same identity cascade as the sample source organism
        (Wikidata IRI -> OTT-keyed EMI_RES -> None).
        """
        return _organism_uri(organism.wikidata, organism.ott_id)
    
    
