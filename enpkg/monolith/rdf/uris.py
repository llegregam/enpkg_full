import hashlib
from typing import Optional
from urllib.parse import quote

from rdflib import URIRef

from ..data.analysis import Analysis
from ..data.annotated_spectra_class import AnnotatedSpectrum
from ..data.chemical_annotation import AnnotationOrganism, MS2ChemicalAnnotation
from ..data.lotus_class import Lotus
from ..data.ms1_data_classes.adduct_class import AdductRecipe, ChemicalAdduct
from ..data.otl_class import Match
from ..data.sirius_annotation import SiriusChemicalAnnotation
from .namespaces import EMI_RES, INCHIKEY


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
        A ``ChemicalAdduct`` *is* a (LOTUS formula group, recipe) pairing — the MS1
        enhancer builds exactly one per pair — so **both** halves belong in the key.
        The recipe alone is not a key: keying on it merged every molecule proposed
        for one feature under one ionization form onto a single node, which then
        kept the first hypothesis' masses and candidate structures while collecting
        a conflicting ``enpkg:annotationRank`` from each of the others.

        The formula rides as a readable segment (``.../<recipe hash>/C9H8O4``)
        rather than as a second opaque hash: an adduct IRI is something humans read
        in the Turtle, and the formula is exactly the discriminator they need to
        see. It is percent-encoded — LOTUS formulae are plain ASCII in practice,
        but nothing validates them and one stray ``/`` would reshape the IRI path.

        ``_recipe_hash`` is deliberately reused unchanged: it also keys the globally
        shared recipe nodes (``recipe_uri``), so widening it would move every recipe
        IRI too, for no gain.

        Never str(adduct) — that's an unstable Pydantic repr, not a stable key.
        """
        return EMI_RES[
            f"adduct/{analysis.run_name}/{spectrum.feature_id}"
            f"/{_recipe_hash(adduct.recipe)}/{_formula_segment(adduct)}"
        ]

    # MS1 ADDUCT-CLUSTER URIs
    @staticmethod
    def adduct_cluster_uri(analysis: Analysis, cluster_id: int) -> URIRef:
        """Mint the URI for one resolved MS1 adduct cluster (a molecule and its adducts).

        Run-scoped: ``cluster_id`` (from the graph enhancer's ``resolve_clusters``) is
        only unique *within* a run, so the URI composes run_name + cluster_id — the
        same pattern as the per-spectrum URI. Stable across re-serializations.
        """
        return EMI_RES[f"adductcluster/{analysis.run_name}/{cluster_id}"]

    # MOLECULAR-NETWORK URIs
    @staticmethod
    def lfpair_uri(analysis: Analysis, feature_id_a, feature_id_b) -> URIRef:
        """Mint the URI for one reified molecular-network edge (an ``emi:LFpair``).

        The pair is *unordered* — the network is an undirected graph, so ``(u, v)``
        comes out of networkx in arbitrary orientation — hence the key is the two
        feature ids **sorted**: ``lfpair/{run}/{lo}_{hi}``. Sorting at all is what
        makes the URI orientation-independent (the same edge always mints the same
        node, so re-serializing is a no-op); sorting *numerically* rather than
        lexicographically is what keeps 9 below 10, since node ids reach us as
        strings. Run-scoped like spectrum_uri: a feature id is only unique
        within a run.
        """
        low, high = sorted((int(feature_id_a), int(feature_id_b)))
        return EMI_RES[f"lfpair/{analysis.run_name}/{low}_{high}"]

    @staticmethod
    def fbmn_component_uri(analysis: Analysis, min_feature_id) -> URIRef:
        """Mint the URI for one connected component of the molecular network.

        A component has no identifier of its own (unlike an MS1 adduct cluster,
        which the graph enhancer numbers), so it is keyed on its smallest member
        feature id, compared **numerically** — a canonical, membership-derived
        representative that does not depend on iteration order.

        Caveat (the same one adduct_cluster_uri carries, but it bites harder here):
        the key is a function of the component's membership, so recomputing the
        network with different NetworkEnhancer parameters can put a *different*
        component behind the same URI. Merging two differently-parameterised
        exports of one run is therefore not meaningful.
        """
        return EMI_RES[f"fbmncomponent/{analysis.run_name}/{int(min_feature_id)}"]

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

    # SIRIUS ANNOTATION URIs
    @staticmethod
    def sirius_annotation_uri(
        analysis: Analysis,
        spectrum: AnnotatedSpectrum,
        annotation: SiriusChemicalAnnotation,
    ) -> URIRef:
        """Mint the URI for one SIRIUS structure-identification annotation on a spectrum.

        Run-scoped and per-spectrum, like the MS2 match event: SIRIUS proposes a
        ranked candidate structure for a given feature in a given run. Keyed on the
        matched structure (2D InChIKey) — the stable identity of the candidate — so
        re-serializing is idempotent; the SIRIUS rank rides along as a property, not
        as a node. Sharing happens one level down, at the InChIKey2D node this
        annotation points to (the same node MS1 compounds and MS2 matches reuse).
        """
        return EMI_RES[
            f"sirius/{analysis.run_name}/{spectrum.feature_id}/{annotation.inchikey_2d}"
        ]

    # CANOPUS CLASSIFICATION URIs
    @staticmethod
    def canopus_annotation_uri(analysis: Analysis, spectrum: AnnotatedSpectrum) -> URIRef:
        """Mint the URI for the CANOPUS class prediction on a spectrum.

        Run-scoped and per-spectrum, and — unlike the SIRIUS structure annotation above —
        needing no candidate key: CANOPUS emits exactly one classification per feature, so
        the feature identifies it. The predicted terms ride along as npc: object
        properties, not as part of the URI, so re-running CANOPUS with a newer model
        updates the same node rather than orphaning it.
        """
        return EMI_RES[f"canopus/{analysis.run_name}/{spectrum.feature_id}"]

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


def _formula_segment(adduct: ChemicalAdduct) -> str:
    """IRI-safe segment naming the LOTUS formula group behind one adduct hypothesis.

    ``ChemicalAdduct.validate_lotus`` guarantees every entry of the group shares a
    molecular formula, and ``LotusStore`` builds exactly one group per formula, so
    the formula names the whole group faithfully and separates any two hypotheses
    one feature can carry under one recipe.

    ``Lotus`` is a plain (unvalidated) dataclass fed from a scraped database, so the
    formula can arrive as None, NaN or blank. The cascade below never raises and
    never silently merges two groups:

    1. the formula, percent-encoded — nothing validates a formula, and a single
       stray ``/`` or space would otherwise reshape the IRI path;
    2. else the group's own exact mass, which *defines* a formula group (same
       formula => same exact mass) and is load-bearing for the whole MS1 path, so
       it cannot be missing where a formula can be;
    3. else a hash of the group's short InChIKey — last resort, since it names one
       member rather than the group.

    Step 3 is not paranoia: ``f"mass-{None:.6f}"`` raises TypeError, and both GUI
    export paths wrap serialization in a bare except, so one junk LOTUS row would
    lose the *entire* file. Same reasoning as ``_resolve_node`` in the serializer.
    """
    formula = adduct.molecular_formula
    if isinstance(formula, str) and formula.strip():
        return quote(formula.strip(), safe="")
    mass = adduct.neutral_mass
    if isinstance(mass, (int, float)) and mass == mass:  # rejects None and NaN
        return f"mass-{mass:.6f}"
    return f"group-{_short_hash(str(adduct.short_inchikey))}"


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


