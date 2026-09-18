"""Serialize an `Analysis` object hierarchy into an RDF graph.

Walks an :class:`Analysis`, mints node URIs via :mod:`enpkg.monolith.rdf.uris`,
and emits triples using the vocabulary mapping recorded in
``docs/RDF_DATA_MODEL_mapped.md``.

Public API::

    s = AnalysisSerializer()
    s.add_analysis(analysis)          # idempotent across calls (shared-node dedup)
    ttl = s.graph.serialize(format="turtle")
    serialize_to_turtle(analysis, "out.ttl")

Phases implemented here:
  1. ``Analysis -> Sample -> FeatureSet -> Spectrum`` spine + shared Taxon node.
  2. MS1 adducts (+ recipe), compounds (+ 2D-InChIKey bridge node, organism,
     reference) and MS2 annotations.
  3. The OTT match node, the molecular network (``emi:LFpair`` edges; plus
     ``emi:FBMNComponent`` nodes for the connected components those edges form, on by
     default) and the gated product-ion layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

import networkx as nx
import numpy as np
from rdflib import Graph, Literal, URIRef

from ..data.analysis import Analysis
from ..data.annotated_spectra_class import AnnotatedSpectrum
from ..data.chemical_annotation import MS2ChemicalAnnotation
from ..data.lotus_class import Lotus
from ..data.ms1_data_classes.adduct_class import AdductRecipe, ChemicalAdduct
from ..data.sirius_annotation import SiriusChemicalAnnotation
from .namespaces import (
    CHEMROF,
    DCTERMS,
    DOI,
    EMI,
    EMI_RES,
    ENPKG,
    GBIF,
    INCHIKEY,
    MASSIVE,
    MS,
    NCBITAXON,
    NCBITAXON_PROP,
    NPC,
    OWL,
    PROV,
    PUBCHEM,
    RDF,
    RDFS,
    SKOS,
    SOSA,
    WD,
    XSD,
)
from .npc_vocabulary import npc_vocabulary
from .uris import AnalysisURIs, CompoundURIs, OrganismURIs, _organism_uri

# Prefixes bound on the output graph (cosmetic — controls Turtle prefix display).
_PREFIXES = {
    "emi": EMI, "enpkg": ENPKG, "npc": NPC, "sosa": SOSA, "ms": MS, "chemrof": CHEMROF,
    "taxon": NCBITAXON, "ncbitaxon": NCBITAXON_PROP,
    "prov": PROV, "dcterms": DCTERMS, "skos": SKOS, "emi-res": EMI_RES,
    "wd": WD, "inchikey": INCHIKEY, "pubchem": PUBCHEM, "gbif": GBIF, "doi": DOI,
    "massive": MASSIVE,
}

# The single source of truth for every enpkg: term's rdf:type / rdfs:domain /
# rdfs:range / skos:exactMatch etc. — see docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md,
# Group F D3. serializer.py is 3 directories below the repo root (rdf -> monolith ->
# enpkg -> root), same depth as the drift test that also resolves this path.
_VOCAB_PATH = Path(__file__).resolve().parents[3] / "docs" / "vocab" / "enpkg.ttl"

# Ingredient name -> chemical symbol, for rendering emi:hasAdduct strings.
_ADDUCT_SYMBOLS = {
    "proton": "H", "ammonium": "NH4", "water": "H2O", "sodium": "Na",
    "magnesium": "Mg", "methanol": "CH3OH", "chlorine": "Cl", "potassium": "K",
    "calcium": "Ca", "acetonitrile": "ACN", "ethylamine": "EtNH2", "formic": "FA",
    "iron": "Fe", "acetic": "Hac", "isopropanol": "IsoProp", "dmso": "DMSO",
    "bromine": "Br", "tfa": "TFA",
}


class AnalysisSerializer:
    """Accumulates triples for one or more `Analysis` objects into ``self.graph``."""

    def __init__(
        self,
        *,
        top_k_ms1: Optional[int] = 5,
        top_k_ms2: Optional[int] = 5,
        top_k_sirius: Optional[int] = None,
        include_fbmn_components: bool = True,
        include_ions: bool = False,
        include_adduct_clusters: bool = True,
        min_relative_intensity: float = 0.0,
        max_ions_per_spectrum: Optional[int] = None,
    ):
        self.graph = Graph()
        for prefix, namespace in _PREFIXES.items():
            self.graph.bind(prefix, namespace)
        self._declare_vocabulary()
        # URIs of shared nodes already described (compounds, organisms, recipes,
        # references, 2D InChIKeys) so they are emitted once even across repeated
        # add_analysis calls.
        self._emitted: set[URIRef] = set()
        # Cap annotations per spectrum at the top-k best (default 5); a non-positive
        # value disables the cap (emit all).
        self.top_k_ms1 = top_k_ms1 if (top_k_ms1 is None or top_k_ms1 > 0) else None
        self.top_k_ms2 = top_k_ms2 if (top_k_ms2 is None or top_k_ms2 > 0) else None
        # SIRIUS candidates arrive pre-ranked (structurePerIdRank) and the summary
        # file is already SIRIUS's chosen top-X, so this defaults to None (emit all).
        self.top_k_sirius = top_k_sirius if (top_k_sirius is None or top_k_sirius > 0) else None
        # Components are O(features) where the edges they derive from are O(features^2),
        # and the component is the unit FBMN consumers reason about. A no-op without a
        # network.
        self.include_fbmn_components = include_fbmn_components
        self.include_ions = include_ions
        self.include_adduct_clusters = include_adduct_clusters
        self.min_relative_intensity = min_relative_intensity
        self.max_ions_per_spectrum = max_ions_per_spectrum

    # ------------------------------------------------------------------ helpers
    def _set(self, subject: URIRef, predicate, value, datatype=None) -> None:
        """Emit ``(subject, predicate, Literal(value))`` unless value is empty.

        Skips ``None``, NaN floats and empty strings so missing data produces no
        triple rather than a junk literal.
        """
        if value is None:
            return
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):  # NaN/inf
            return
        if isinstance(value, str) and value.strip() == "":
            return
        literal = Literal(value, datatype=datatype) if datatype is not None else Literal(value)
        self.graph.add((subject, predicate, literal))

    @staticmethod
    def _as_int(value) -> Optional[int]:
        """Return ``int(value)`` or ``None`` for None/NaN (LOTUS ids arrive as floats)."""
        if value is None:
            return None
        if isinstance(value, float) and value != value:  # NaN
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _massive_uri(massive_id) -> Optional[URIRef]:
        """Build the ``emi:hasMassiveDOI`` object URI from ``metadata.massive_id``.

        EMI declares ``hasMassiveDOI`` an ``owl:ObjectProperty`` — its own example
        points it at a resolvable MassIVE dataset URL, not a literal accession
        string. ``massive_id`` normally arrives as a bare accession (e.g.
        ``"MSV000087728"``), turned into ``https://massive.ucsd.edu/ProteoSAFe/
        dataset.jsp?accession=MSV000087728`` via the ``MASSIVE`` namespace; a
        value that's already a full URL (someone pasted the whole link into the
        metadata sheet) is kept as-is rather than double-prefixed.
        """
        if massive_id is None:
            return None
        massive_id = str(massive_id).strip()
        if not massive_id:
            return None
        if massive_id.startswith(("http://", "https://")):
            return URIRef(massive_id)
        return MASSIVE[massive_id]

    def _declare_vocabulary(self) -> None:
        """Load every enpkg: term declaration from the single source of truth
        (docs/vocab/enpkg.ttl) into the output graph, once per graph.

        Replaces what used to be six methods spelling out each term's
        rdf:type/rdfs:domain/rdfs:range/skos:exactMatch as Python literals — a
        second, driftable copy of what enpkg.ttl already declares (see
        docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md, Group F D3). Every export
        stays exactly as self-contained as before (the full vocabulary is still
        inlined into every ``.ttl`` file); only the source of the declarations
        changed. This brings in the ontology's own header triple too
        (``enpkg: a owl:Ontology ; owl:imports emi: ; ...``) — a statement that
        EMI's axioms apply, not an actual fetch of them; ``graph.parse`` reads
        only this one file.
        """
        self.graph.parse(_VOCAB_PATH, format="turtle")

    @staticmethod
    def _format_adduct(recipe: AdductRecipe) -> str:
        """Render a recipe as a standard adduct string: "[M+H]+", "[2M+Na]+",
        "[M+2K-H]+" — the value for emi:hasAdduct (cf. PSI-MS MS_1002813 form)."""
        def _int(x):
            return int(x) if float(x).is_integer() else x
        mult = _int(recipe.multimer_factor)
        core = f"{'' if mult == 1 else mult}M"
        parts = []
        for name, count in sorted(recipe.ingredients.items()):
            c = _int(count)
            if c == 0:
                continue
            symbol = _ADDUCT_SYMBOLS.get(name, name)
            magnitude = "" if abs(c) == 1 else abs(c)
            parts.append(f"{'+' if c > 0 else '-'}{magnitude}{symbol}")
        charge = _int(recipe.charge)
        charge_str = "" if charge == 1 else charge
        return f"[{core}{''.join(parts)}]{charge_str}{'+' if recipe.positive else '-'}"

    # ------------------------------------------------------------------- public
    def add_analysis(self, analysis: Analysis) -> URIRef:
        """Add all triples for ``analysis`` to the graph and return its URI."""
        return self._add_analysis(analysis)

    # ----------------------------------------------------------- spine (Phase 1)
    def _add_analysis(self, analysis: Analysis) -> URIRef:
        uri = AnalysisURIs.analysis_uri(analysis)
        g = self.graph
        g.add((uri, RDF.type, EMI.LCMSAnalysis))
        ionization_mode = (analysis.ionization_mode or "").lower()
        if ionization_mode.startswith("pos"):
            g.add((uri, RDF.type, EMI.LCMSAnalysisPos))
        elif ionization_mode.startswith("neg"):
            g.add((uri, RDF.type, EMI.LCMSAnalysisNeg))
        self._set(uri, DCTERMS.identifier, analysis.run_name)
        massive_uri = self._massive_uri(getattr(analysis.metadata, "massive_id", None))
        if massive_uri is not None:
            g.add((uri, EMI.hasMassiveDOI, massive_uri))

        g.add((self._add_sample(analysis), ENPKG.hasLabProcess, uri))
        featureset_uri = self._add_featureset(analysis)
        g.add((uri, EMI.hasLCMSFeatureSet, featureset_uri))
        # Adduct clusters group features, so they hang off the feature set (which also
        # minted the member feature URIs just above), not the analysis directly.
        if self.include_adduct_clusters:
            self._add_adduct_clusters(analysis, featureset_uri)
        for match in (analysis.ott_matches or []):
            g.add((uri, ENPKG.hasOTTMatch, self._add_match(analysis, match)))
        # Network components also group features, so like the adduct clusters above
        # they hang off the feature set — and both layers reference the member
        # feature nodes it just minted, so this must stay after _add_featureset.
        self._add_network_layer(analysis, featureset_uri)
        return uri

    def _add_sample(self, analysis: Analysis) -> URIRef:
        uri = AnalysisURIs.analysis_metadata_uri(analysis)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g, md = self.graph, analysis.metadata
        g.add((uri, RDF.type, EMI.ExtractSample))
        self._set(uri, DCTERMS.identifier, md.sample_id)
        self._set(uri, RDFS.label, md.source_taxon)
        self._set(uri, ENPKG.sourceId, md.source_id)
        self._set(uri, ENPKG.sampleFilenamePos, md.sample_filename_pos)
        self._set(uri, ENPKG.sampleFilenameNeg, md.sample_filename_neg)

        organism_uri = AnalysisURIs.source_organism_uri(analysis)
        if organism_uri is not None:
            g.add((uri, SOSA.isSampleOf, organism_uri))
            taxon = analysis.best_ott_matches.taxon
            self._add_taxon(
                organism_uri,
                scientific_name=taxon.name,
                wikidata=(taxon.wikidata.wd if taxon.wikidata is not None else None),
                ott_id=taxon.open_tree_taxon_id,
            )
        return uri

    def _add_featureset(self, analysis: Analysis) -> URIRef:
        uri = AnalysisURIs.featureset_uri(analysis)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.LCMSFeatureSet))
        for spectrum in analysis.spectra:
            g.add((uri, EMI.hasLCMSFeature, self._add_spectrum(analysis, spectrum)))
        return uri

    def _add_spectrum(self, analysis: Analysis, spectrum: AnnotatedSpectrum) -> URIRef:
        uri = AnalysisURIs.spectrum_uri(analysis, spectrum)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.LCMSFeature))
        self._set(uri, EMI.hasRowId, spectrum.feature_id)
        self._set(uri, EMI.hasParentMass, spectrum.precursor_mz)
        self._set(uri, EMI.hasRetentionTime, spectrum.retention_time)
        self._set(uri, EMI.hasFeatureArea, spectrum.intensity)
        # TODO: Look into the ranking of annotations. Should think of when we want to use
        # simple Cosine ranking vs. the more complex reweighted NPC-alignment score
        #
        # MS2 is emitted first so MS1 can be *coupled to* it: an MS2 match identifies a
        # specific compound by fragmentation, and the MS1 adduct proposing that same
        # compound is the one that explains its ionization. See _emit_ms1_annotations.
        ms2_emitted = self._add_ranked_annotations(
            uri, spectrum.ms2_annotations,
            scores=(spectrum.ms2_pathway_scores, spectrum.ms2_superclass_scores, spectrum.ms2_class_scores),
            top_k=self.top_k_ms2,
            emit=lambda ann: self._add_ms2_annotation(analysis, spectrum, ann),
            fallback_key=lambda ann: ann.score,  # cosine, when NPC scores absent
        )
        self._emit_ms1_annotations(analysis, spectrum, uri, ms2_emitted)
        self._add_sirius_annotations(analysis, spectrum, uri)
        self._add_canopus_classification(analysis, spectrum, uri)
        if self.include_ions:
            self._add_ions(uri, spectrum)
        return uri

    def _emit_ms1_annotations(
        self, analysis, spectrum, spectrum_uri, ms2_emitted
    ) -> None:
        """Emit the feature's MS1 adducts, coupled to any MS2 matches.

        Two regimes:

        * **No serialized MS2** (``ms2_emitted`` empty) — emit MS1 as mass-only
          hypotheses, ranked and capped at ``top_k_ms1`` (unchanged behaviour).
        * **MS2-identified feature** — an MS2 match names a compound by
          fragmentation, so only the MS1 adducts whose candidate structures include
          an MS2-matched compound (shared 2D short InChIKey) are meaningful. Keep
          *every* such adduct (all forms; ``top_k`` off), drop the mass-coincidence
          rest, and link each MS2 annotation to its corresponding adduct via
          ``enpkg:hasCorrespondingAdduct``. A feature whose MS2 compound appears in
          no MS1 group is therefore serialized with no MS1 adduct — intended (see
          ``docs/MS2_ENHANCER.md`` §6 caveat).
        """
        ms1_scores = (
            spectrum.ms1_pathway_scores,
            spectrum.ms1_superclass_scores,
            spectrum.ms1_class_scores,
        )
        # short InChIKey -> the MS2 annotation URIs that matched that compound.
        ms2_by_short_ik: dict[str, list[URIRef]] = {}
        for _score, annotation, ms2_uri in ms2_emitted:
            ms2_by_short_ik.setdefault(annotation.short_inchikey, []).append(ms2_uri)

        if not ms2_by_short_ik:
            self._add_ranked_annotations(
                spectrum_uri, spectrum.ms1_annotations,
                scores=ms1_scores, top_k=self.top_k_ms1,
                emit=lambda ann: self._add_chemical_adduct(analysis, spectrum, ann),
            )
            return

        corresponding = [
            adduct for adduct in spectrum.ms1_annotations
            if {lotus.short_inchikey for lotus in adduct.lotus} & ms2_by_short_ik.keys()
        ]
        ms1_emitted = self._add_ranked_annotations(
            spectrum_uri, corresponding,
            scores=ms1_scores, top_k=None,  # keep every corresponding adduct form
            emit=lambda ann: self._add_chemical_adduct(analysis, spectrum, ann),
        )
        for _score, adduct, adduct_uri in ms1_emitted:
            shared = {lotus.short_inchikey for lotus in adduct.lotus} & ms2_by_short_ik.keys()
            for short_ik in shared:
                for ms2_uri in ms2_by_short_ik[short_ik]:
                    self.graph.add((ms2_uri, ENPKG.hasCorrespondingAdduct, adduct_uri))

    @staticmethod
    def _alignment_score(annotation, pathway, superclass, klass) -> float:
        """Reweighted NPC-alignment score: how well a candidate's classification
        matches the spectrum's propagated NPC distribution (the same product the
        get_top_k_* helpers rank by). Returns -inf if the arrays don't line up."""
        try:
            return float(
                np.mean(annotation.get_pathway_scores() * pathway)
                * np.mean(annotation.get_superclass_scores() * superclass)
                * np.mean(annotation.get_class_scores() * klass)
            )
        except Exception:  # noqa: BLE001 - absent/mismatched arrays -> unrankable
            return float("-inf")

    def _add_ranked_annotations(
        self, spectrum_uri, annotations, *, scores, top_k, emit, fallback_key=None
    ) -> list[tuple[Optional[float], object, URIRef]]:
        """Emit a spectrum's annotations ranked by reweighted score, capped at
        top_k. Tags each kept node with enpkg:annotationRank (1=best) +
        enpkg:annotationScore. When the propagated NPC scores are absent (weights
        enhancer not run), falls back to ``fallback_key`` (e.g. MS2 cosine) for
        ranking; if that's also absent, keeps stored order. The cap is always
        applied so top_k means top_k, ranked or not.

        Returns the emitted ``(score, annotation, annotation_uri)`` triples (in
        rank order), so callers can wire cross-annotation links (e.g. couple MS2
        matches to their corresponding MS1 adducts); ``[]`` when nothing is emitted."""
        if not annotations:
            return []
        pathway, superclass, klass = scores
        rankable = pathway is not None and superclass is not None and klass is not None
        if rankable:
            ranked = sorted(
                ((self._alignment_score(a, pathway, superclass, klass), a) for a in annotations),
                key=lambda pair: pair[0],
                reverse=True,
            )
        elif fallback_key is not None:
            ranked = sorted(
                ((fallback_key(a), a) for a in annotations),
                key=lambda pair: pair[0],
                reverse=True,
            )
        else:
            # No score to rank by — keep stored order, no annotationScore.
            ranked = [(None, a) for a in annotations]
        if top_k is not None:
            ranked = ranked[:top_k]
        emitted: list[tuple[Optional[float], object, URIRef]] = []
        for rank, (score, annotation) in enumerate(ranked, start=1):
            annotation_uri = emit(annotation)
            self.graph.add((spectrum_uri, EMI.hasAnnotation, annotation_uri))
            self._set(annotation_uri, ENPKG.annotationRank, rank)
            self._set(annotation_uri, ENPKG.annotationScore, score)
            emitted.append((score, annotation, annotation_uri))
        return emitted

    def _add_taxon(
        self,
        uri: URIRef,
        *,
        scientific_name: Optional[str] = None,
        wikidata: Optional[str] = None,
        ott_id=None,
        ncbi_id=None,
        gbif_id=None,
        rank: Optional[str] = None,
    ) -> URIRef:
        """Emit (once) a shared organism/taxon node.

        Used by the sample source, Lotus-organism and MS2-annotation paths; all
        resolve to one node via the shared minting cascade, so they dedup here.
        """
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.Taxon))
        self._set(uri, EMI.scientificName, scientific_name)
        self._set(uri, DCTERMS.identifier, self._as_int(ott_id))

        externals = []
        if wikidata:
            externals.append(URIRef(wikidata))
        if self._as_int(ncbi_id) is not None:
            externals.append(NCBITAXON[str(self._as_int(ncbi_id))])
        if self._as_int(gbif_id) is not None:
            externals.append(GBIF[str(self._as_int(gbif_id))])
        for external in externals:
            if external != uri:
                g.add((uri, OWL.sameAs, external))

        if rank:
            g.add((uri, NCBITAXON_PROP.has_rank, NCBITAXON[str(rank)]))
        return uri

    # ----------------------------------------------- annotations etc. (Phase 2)
    def _add_chemical_adduct(
        self, analysis: Analysis, spectrum: AnnotatedSpectrum, adduct: ChemicalAdduct
    ) -> URIRef:
        uri = AnalysisURIs.chemical_adduct_uri(analysis, spectrum, adduct)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.StructuralAnnotation))
        g.add((uri, RDF.type, ENPKG.AdductAnnotation))              # MS1-specific subclass
        # Polarity-specific PSI-MS adduct-ion class only; its parent MS:1000353
        # ("adduct ion") is entailed, so we don't emit it redundantly.
        g.add((uri, RDF.type, MS["1002807" if adduct.recipe.positive else "1002808"]))  # polarity
        self._set(uri, ENPKG.adductMass, adduct.adduct_mass)        # skos:exactMatch MS:1003243
        self._set(uri, ENPKG.adductNeutralMass, adduct.neutral_mass)  # skos:closeMatch chemrof:monoisotopic_mass
        self._set(uri, EMI.hasAdduct, self._format_adduct(adduct.recipe))  # "[M+H]+" form (EMI property)
        g.add((uri, ENPKG.hasRecipe, self._add_recipe(adduct.recipe)))
        for lotus in adduct.lotus:
            # Sibling of emi:hasChemicalStructure (used by MS2/SIRIUS), deliberately not a
            # subproperty: MS1 candidates are mass-coincidence hits, not confirmed
            # identifications — see docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md §1b.
            g.add((uri, ENPKG.hasCandidateStructure, self._add_compound(lotus)))
        return uri

    def _add_recipe(self, recipe: AdductRecipe) -> URIRef:
        uri = AnalysisURIs.recipe_uri(recipe)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, ENPKG.AdductRecipe))
        self._set(uri, ENPKG.charge, recipe.charge)                 # skos:exactMatch MS:1000041
        self._set(uri, ENPKG.isPositive, recipe.positive)
        self._set(uri, ENPKG.multimerFactor, recipe.multimer_factor)
        for name, count in recipe.ingredients.items():
            # Deterministic IRI (not a blank node): the recipe is a globally
            # shared node, so blank-node ingredients would duplicate on every
            # re-serialization (one set per file). A stable per-(recipe,name) IRI
            # merges across files. Ingredient names are unique within a recipe.
            ingredient = URIRef(f"{uri}/ingredient/{quote(str(name), safe='')}")
            g.add((uri, ENPKG.hasIngredient, ingredient))
            self._set(ingredient, ENPKG.ingredientName, name)
            self._set(ingredient, ENPKG.ingredientCount, count)
        return uri

    def _add_adduct_clusters(self, analysis: Analysis, featureset_uri: URIRef) -> None:
        """Emit one ``enpkg:AdductCluster`` node per resolved MS1 adduct cluster.

        A cluster is *implicit* in memory: the MS1 graph enhancer stamps each member
        spectrum with a shared ``ms1_cluster_id`` and identical CGC/CIC/CCC copies,
        plus an ``ms1_cluster_role`` and ``ms1_assigned_recipe``. Here we regroup the
        spectra by cluster id to materialise the molecule as an explicit node (its
        anchor base ion + satellite adducts), so "all adducts of one compound" is a
        one-hop query. Clusters group features, so each hangs off the ``featureset_uri``
        via ``enpkg:hasAdductCluster``. Singletons (``ms1_cluster_id is None``) get no
        cluster node. Keys off the per-spectrum stamps only — the ``ms1_adduct_graph``
        need not be set.
        """
        clusters: dict[int, list[AnnotatedSpectrum]] = {}
        for spectrum in analysis.spectra:
            cluster_id = spectrum.ms1_cluster_id
            if cluster_id is None:
                continue  # singleton — absence of membership *is* "singleton"
            clusters.setdefault(cluster_id, []).append(spectrum)

        g = self.graph
        for cluster_id, members in clusters.items():
            cluster_uri = AnalysisURIs.adduct_cluster_uri(analysis, cluster_id)
            g.add((featureset_uri, ENPKG.hasAdductCluster, cluster_uri))
            if cluster_uri in self._emitted:
                continue
            self._emitted.add(cluster_uri)
            g.add((cluster_uri, RDF.type, ENPKG.AdductCluster))
            # CGC/CIC/CCC are identical across members, so any member is representative.
            representative = members[0]
            self._set(cluster_uri, ENPKG.clusterConnectivity, representative.ms1_cluster_connectivity)
            self._set(cluster_uri, ENPKG.clusterIntensityCoverage, representative.ms1_cluster_intensity_coverage)
            self._set(cluster_uri, ENPKG.clusterCountCoverage, representative.ms1_cluster_count_coverage)
            for member in members:
                member_uri = AnalysisURIs.spectrum_uri(analysis, member)
                g.add((cluster_uri, ENPKG.hasClusterMember, member_uri))
                if member.ms1_cluster_role == "anchor":
                    g.add((cluster_uri, ENPKG.hasAnchor, member_uri))
                # Per-feature resolution: role, back-link, and the resolved ionization
                # form as a literal (e.g. "[M+Na]+") — not a parallel edge to a node.
                # The form is already carried by the feature's MS1 annotations
                # (emi:hasAnnotation), so a string here avoids a redundant recipe node.
                self._set(member_uri, ENPKG.clusterRole, member.ms1_cluster_role)
                g.add((member_uri, ENPKG.inAdductCluster, cluster_uri))
                if member.ms1_assigned_recipe is not None:
                    self._set(member_uri, ENPKG.resolvedAdduct,
                              self._format_adduct(member.ms1_assigned_recipe))

    def _add_compound(self, lotus: Lotus) -> URIRef:
        uri = CompoundURIs.lotus_uri(lotus)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.ChemicalStructure))
        # Full InChIKey kept as a literal (the compound IRI already encodes it);
        # only the 2D node is materialised, to bridge MS1 (full) and MS2 (short).
        self._set(uri, CHEMROF.inchi_key_string, lotus.structure_inchikey)
        self._set(uri, EMI.hasSMILES, lotus.structure_smiles)
        self._set(uri, CHEMROF.inchi_string, lotus.structure_inchi)
        self._set(uri, CHEMROF.generalized_empirical_formula, lotus.structure_molecular_formula)
        self._set(uri, CHEMROF.monoisotopic_mass, lotus.structure_exact_mass)
        self._set(uri, ENPKG.xlogp, lotus.structure_xlogp)
        self._set(uri, ENPKG.stereocentersTotal, lotus.structure_stereocenters_total)
        self._set(uri, ENPKG.stereocentersUnspecified, lotus.structure_stereocenters_unspecified)
        self._set(uri, ENPKG.manualValidation, lotus.manual_validation)
        self._set(uri, SKOS.prefLabel, lotus.structure_name_traditional)
        self._set(uri, SKOS.altLabel, lotus.structure_name_iupac)
        # ClassyFire ranks as literals (ChemOnt node linking deferred).
        self._set(uri, ENPKG.classyfireKingdom, lotus.structure_taxonomy_classyfire_01kingdom)
        self._set(uri, ENPKG.classyfireSuperclass, lotus.structure_taxonomy_classyfire_02superclass)
        self._set(uri, ENPKG.classyfireClass, lotus.structure_taxonomy_classyfire_03class)
        self._set(uri, ENPKG.classyfireDirectParent, lotus.structure_taxonomy_classyfire_04directparent)

        cid = self._as_int(lotus.structure_cid)
        if cid is not None:
            self._same_as(uri, PUBCHEM[str(cid)])
        if lotus.structure_wikidata:
            self._same_as(uri, URIRef(lotus.structure_wikidata))

        g.add((uri, EMI.hasInChIKey2D, self._inchikey2d_node(lotus.short_inchikey)))

        organism_uri = _organism_uri(lotus.organism_wikidata, lotus.organism_taxonomy_ottid)
        if organism_uri is not None:
            g.add((uri, EMI.inTaxon, self._add_taxon(
                organism_uri,
                scientific_name=lotus.organism_name,
                wikidata=lotus.organism_wikidata,
                ott_id=lotus.organism_taxonomy_ottid,
                ncbi_id=lotus.organism_taxonomy_ncbiid,
                gbif_id=lotus.organism_taxonomy_gbifid,
            )))

        reference_uri = self._add_reference(lotus)
        if reference_uri is not None:
            g.add((uri, PROV.wasDerivedFrom, reference_uri))
        return uri

    def _inchikey2d_node(self, short_inchikey: str) -> URIRef:
        """The 2D-InChIKey structure node shared by MS1 compounds and MS2 matches."""
        uri = EMI_RES[f"inchikey2d/{short_inchikey}"]
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        self.graph.add((uri, RDF.type, EMI.InChIKey2D))
        self._set(uri, EMI.inChIKey2D, short_inchikey)
        return uri

    def _add_reference(self, lotus: Lotus) -> Optional[URIRef]:
        doi = getattr(lotus, "reference_doi", None)
        if not doi:
            return None
        # Legacy DOIs (esp. Wiley) contain <>#;() etc. that are illegal in an IRI
        # path; percent-encode the suffix so the IRI is valid (doi.org still
        # resolves it). Keep the RFC-3986 path-legal characters unencoded.
        uri = DOI[quote(str(doi).strip(), safe="/:@!$&'()*+,;=-._~")]
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.BibliographicResource))
        self._set(uri, DCTERMS.identifier, doi)  # the raw (unencoded) DOI
        if lotus.reference_wikidata:
            self._same_as(uri, URIRef(lotus.reference_wikidata))
        return uri

    def _add_ms2_annotation(
        self, analysis: Analysis, spectrum: AnnotatedSpectrum, annotation: MS2ChemicalAnnotation
    ) -> URIRef:
        uri = AnalysisURIs.ms2_annotation_uri(analysis, spectrum, annotation)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.StructuralAnnotation))
        g.add((uri, RDF.type, ENPKG.SpectralAnnotation))            # MS2-specific subclass
        self._set(uri, EMI.hasSpectralScore, annotation.score)
        self._set(uri, ENPKG.nMatchedPeaks, annotation.n_matched_peaks)
        self._set(uri, DCTERMS.source, annotation.queried_against)
        if annotation.source:
            g.add((uri, PROV.wasDerivedFrom, EMI_RES[f"dataset/{annotation.source}"]))
        # Matched structure attaches at the 2D (short InChIKey) level — the same
        # node the MS1 compound links to via hasInChIKey2D.
        g.add((uri, EMI.hasChemicalStructure, self._inchikey2d_node(annotation.short_inchikey)))
        for organism in annotation.organisms:
            organism_uri = OrganismURIs.organism_uri(organism)
            if organism_uri is not None:
                g.add((uri, EMI.inTaxon, self._add_taxon(
                    organism_uri,
                    scientific_name=organism.name,
                    wikidata=organism.wikidata,
                    ott_id=organism.ott_id,
                )))
        return uri

    # ------------------------------------------------------- SIRIUS annotations
    def _add_sirius_annotations(
        self, analysis: Analysis, spectrum: AnnotatedSpectrum, spectrum_uri: URIRef
    ) -> None:
        """Link a spectrum to its SIRIUS candidates via emi:hasAnnotation.

        Unified with the MS1/MS2 attach predicate (see §1c of
        docs/SCHEMA_REVIEW_AND_VOCABULARY_PLAN.md): all three channels subclass
        emi:StructuralAnnotation already, so the dedicated enpkg:hasSiriusAnnotation
        predicate was only historical baggage, not a real distinction. A consumer
        tells the channels apart by rdf:type (enpkg:SiriusAnnotation), the same way
        it already must for AdductAnnotation vs SpectralAnnotation.

        SIRIUS emits candidates already ranked (structurePerIdRank, 1=best), so this
        keeps that order and only caps at top_k_sirius)."""
        annotations = spectrum.sirius_annotations
        if not annotations:
            return
        ranked = sorted(annotations, key=lambda a: a.rank)
        if self.top_k_sirius is not None:
            ranked = ranked[: self.top_k_sirius]
        for annotation in ranked:
            annotation_uri = self._add_sirius_annotation(analysis, spectrum, annotation)
            self.graph.add((spectrum_uri, EMI.hasAnnotation, annotation_uri))

    def _add_sirius_annotation(
        self, analysis: Analysis, spectrum: AnnotatedSpectrum, annotation: SiriusChemicalAnnotation
    ) -> URIRef:
        uri = AnalysisURIs.sirius_annotation_uri(analysis, spectrum, annotation)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.StructuralAnnotation))
        g.add((uri, RDF.type, ENPKG.SiriusAnnotation))             # SIRIUS-specific subclass
        self._set(uri, CHEMROF.generalized_empirical_formula, annotation.molecular_formula)
        self._set(uri, EMI.hasAdduct, annotation.adduct)           # "[M+K]+" form (EMI property)
        self._set(uri, ENPKG.annotationRank, annotation.rank)      # SIRIUS structurePerIdRank (1=best)
        # Candidate structure attaches at the 2D (short InChIKey) level — the same node
        # MS1 compounds (hasInChIKey2D) and MS2 matches (hasChemicalStructure) reuse.
        g.add((uri, EMI.hasChemicalStructure, self._inchikey2d_node(annotation.inchikey_2d)))
        return uri

    # ---------------------------------------------------- CANOPUS classification
    # NPClassifier rank -> (object property, probability property). Ordered broad to
    # specific, matching the skos:broader chain the terms themselves carry.
    _NPC_PREDICATES = {
        "Pathway": (EMI.hasPathway, EMI.hasPathwayProbability),
        "Superclass": (EMI.hasSuperClass, EMI.hasSuperClassProbability),
        "Class": (EMI.hasClass, EMI.hasClassProbability),
    }

    def _add_canopus_classification(
        self, analysis: Analysis, spectrum: AnnotatedSpectrum, spectrum_uri: URIRef
    ) -> None:
        """Emit the feature's CANOPUS class prediction as an emi:ChemicalTaxonAnnotation.

        A *separate node* from the spectrum's enpkg:SiriusAnnotation, joined only through
        the shared feature. This is not a stylistic choice: EMI declares
        emi:ChemicalTaxonAnnotation owl:disjointWith emi:StructuralAnnotation, so hanging
        these predicates on the SIRIUS node — tempting, since both describe the same
        feature from the same SIRIUS run — would make the graph inconsistent under any
        reasoner, invalidating every entailment over it, not just this part.

        Attached with emi:hasAnnotation, the same predicate MS1/MS2/SIRIUS use;
        emi:ChemicalTaxonAnnotation is an emi:SpectrumAnnotation, so the range holds.
        A consumer tells the channels apart by rdf:type, as it already must.

        A no-op for features CANOPUS could not classify (~15% of them: it only classifies
        what SIRIUS assigned a molecular formula to).
        """
        classification = spectrum.canopus_classification
        if classification is None:
            return
        ranks = classification.ranks()
        if not ranks:
            return
        uri = AnalysisURIs.canopus_annotation_uri(analysis, spectrum)
        if uri in self._emitted:
            return
        self._emitted.add(uri)
        g = self.graph
        g.add((uri, RDF.type, EMI.ChemicalTaxonAnnotation))
        self._set(uri, CHEMROF.generalized_empirical_formula, classification.molecular_formula)
        self._set(uri, EMI.hasAdduct, classification.adduct)   # "[M+K]+" form, as for SIRIUS
        vocabulary = npc_vocabulary()
        for rank_name, rank in ranks:
            predicate, probability_predicate = self._NPC_PREDICATES[rank_name]
            term = vocabulary.resolve(rank_name, rank.label)
            g.add((uri, predicate, term))
            # Guard on None, never on truthiness: 0.0 is a probability CANOPUS really
            # emits, and dropping it would assert a rank with no confidence attached.
            if rank.probability is not None:
                self._set(uri, probability_predicate, float(rank.probability), datatype=XSD.double)
            self._describe_npc_term(term)
        g.add((spectrum_uri, EMI.hasAnnotation, uri))

    def _describe_npc_term(self, term: URIRef) -> None:
        """Inline a term's type, label and skos:broader ancestry, once per graph.

        Without this the graph carries bare npc: IRIs. _declare_vocabulary inlines
        enpkg.ttl only, and while enpkg.ttl declares owl:imports emi:, an import is a
        pointer rather than content — nothing guarantees a consumer resolves it. The
        failure mode is silent: grouping by rdfs:label or rolling up with skos:broader+
        returns an empty result set that reads like "no such compounds here" rather than
        like a missing vocabulary. Cheap insurance at ~590 triples for a 1160-feature
        analysis (+0.065%). Minted, unvendored terms describe as nothing.
        """
        if term in self._emitted:
            return
        self._emitted.add(term)
        for triple in npc_vocabulary().describe(term):
            self.graph.add(triple)

    # ------------------------------------------------ match/network/ions (Ph 3)
    def _add_match(self, analysis: Analysis, match) -> URIRef:
        uri = AnalysisURIs.ott_match_uri(analysis, match)
        if uri in self._emitted:
            return uri
        self._emitted.add(uri)
        g, taxon = self.graph, match.taxon
        g.add((uri, RDF.type, EMI.Taxon))
        self._set(uri, EMI.scientificName, match.matched_name)
        self._set(uri, DCTERMS.identifier, self._as_int(taxon.open_tree_taxon_id))
        self._set(uri, ENPKG.matchScore, match.score)
        self._set(uri, ENPKG.isApproximateMatch, match.is_approximate_match)
        self._set(uri, ENPKG.isSynonym, match.is_synonym)
        self._set(uri, ENPKG.nomenclatureCode, match.nomenclature_code)
        self._set(uri, ENPKG.searchString, match.search_string)
        if taxon.rank:
            g.add((uri, NCBITAXON_PROP.has_rank, NCBITAXON[str(taxon.rank)]))
        if taxon.wikidata is not None:
            self._same_as(uri, URIRef(taxon.wikidata.wd))
        return uri

    def _add_network_layer(self, analysis: Analysis, featureset_uri: URIRef) -> None:
        """Emit the molecular-network layers. A no-op when there is no network.

        Two views of the same ``nx.Graph``:

        * the reified pairwise edges (``emi:LFpair``), always emitted, worst-case
          quadratic in the number of features.
        * the connected components those edges form (``emi:FBMNComponent``), gated on
          ``include_fbmn_components`` and on by default: one node per family, so
          O(features), and in FBMN practice the component is the unit people reason
          about.

        Builds the feature-id -> spectrum lookup **once** and shares it with both
        emitters. The lookup is needed because network node ids are the MGF
        FEATURE_ID/SCANS values and arrive as *strings* ('9'), while
        AnnotatedSpectrum.feature_id is an int — normalising both sides through int
        is what lets a node find its spectrum, and hence the canonical
        AnalysisURIs.spectrum_uri rather than a hand-built copy of that rule.
        """
        network = analysis.molecular_network
        if network is None:
            return
        spectra_by_id = {spectrum.feature_id: spectrum for spectrum in analysis.spectra}
        self._add_molecular_network(analysis, network, spectra_by_id)
        if self.include_fbmn_components:
            self._add_fbmn_components(analysis, network, featureset_uri, spectra_by_id)

    @staticmethod
    def _resolve_node(
        spectra_by_id: dict[int, AnnotatedSpectrum], node
    ) -> Optional[AnnotatedSpectrum]:
        """Return the spectrum a network node id names, or None if it names none.

        ``Analysis.validate_network_integrity`` makes an unresolvable node impossible
        *at construction* — but the enhancer attaches the graph with
        ``model_copy(update=...)``, and pydantic does not re-run validators there, so
        a mismatched graph can still reach the serializer. Returning None (and
        skipping that edge / member) keeps one bad node from failing an otherwise
        good export: both GUI export paths wrap serialization in a bare except, so
        raising here would lose the entire file.
        """
        try:
            return spectra_by_id.get(int(node))
        except (TypeError, ValueError):
            return None

    def _add_molecular_network(
        self,
        analysis: Analysis,
        network: nx.Graph,
        spectra_by_id: dict[int, AnnotatedSpectrum],
    ) -> None:
        """Emit one named ``emi:LFpair`` node per network edge.

        An RDF triple carries no attributes, so the edge is reified into a node
        holding the modified-cosine score and the precursor-mass difference — the
        modification separating the two features, which is exactly what modified
        cosine already aligned the spectra on.

        The node gets a *named* URI, not a blank node, for the two reasons _add_ions
        gives: blank nodes are not surfaced by GraphDB's browser / visual graph, and
        they are re-minted on every serialization, so merging two exports of one run
        duplicated every edge instead of collapsing it. The URI is keyed on the
        numerically-sorted feature-id pair and the members are emitted in that same
        order, so re-serializing is a no-op and the output no longer depends on which
        way round networkx yielded (u, v).

        "First"/"second" therefore means "lower/higher feature id" — deterministic,
        but still nothing chemical. A consumer asking "what is feature X similar to?"
        should query the emi:hasPairMember superproperty both specialise.

        Deliberately *not* registered in self._emitted: idempotency comes from the
        URI itself (identical triples, RDF set semantics), and one entry per edge
        would make _emitted the largest object in the serializer on a big run.
        """
        g = self.graph
        for u, v, data in network.edges(data=True):
            spectrum_u = self._resolve_node(spectra_by_id, u)
            spectrum_v = self._resolve_node(spectra_by_id, v)
            if spectrum_u is None or spectrum_v is None:
                continue  # node names no spectrum — a one-member pair is malformed
            if spectrum_u.feature_id == spectrum_v.feature_id:
                continue  # self-loop: a feature is not a pair, and hasFirst/SecondMember
                          # are functional, so this would be an OWL inconsistency
            low, high = sorted((spectrum_u, spectrum_v), key=lambda s: s.feature_id)
            pair = AnalysisURIs.lfpair_uri(analysis, low.feature_id, high.feature_id)
            g.add((pair, RDF.type, EMI.LFpair))
            g.add((pair, EMI.hasFirstMember, AnalysisURIs.spectrum_uri(analysis, low)))
            g.add((pair, EMI.hasSecondMember, AnalysisURIs.spectrum_uri(analysis, high)))
            # matchms SimilarityNetwork stores the cosine under "weight".
            score = data.get("weight", data.get("ModifiedCosine_score"))
            self._set(pair, EMI.hasCosine, float(score) if score is not None else None)
            # Absolute, so it does not depend on the first/second assignment.
            self._set(pair, EMI.hasMassDifference, abs(high.precursor_mz - low.precursor_mz))

    def _add_fbmn_components(
        self,
        analysis: Analysis,
        network: nx.Graph,
        featureset_uri: URIRef,
        spectra_by_id: dict[int, AnnotatedSpectrum],
    ) -> None:
        """Emit one ``emi:FBMNComponent`` node per connected component of the network.

        The network's *components*, not its edges, are what FBMN consumers reason
        about: a component is a putative structural family. They are implicit in the
        edge set, so this materialises them — exactly as _add_adduct_clusters
        materialises the MS1 clusters implicit in the per-spectrum stamps. The two
        are declared skos:closeMatch in _declare_adduct_cluster_class: same modelling
        pattern, different notion of relatedness (one molecule ionised several ways
        vs. several structurally related molecules).

        A component has no id of its own, so it is keyed on its smallest member
        feature id compared **numerically** — never min() on the raw node ids, which
        are strings in production, where '10' < '9'. Single-node components are
        skipped: an isolated feature is not a family, mirroring how
        _add_adduct_clusters skips MS1 singletons.
        """
        g = self.graph
        for component in nx.connected_components(network):
            if len(component) < 2:
                continue  # isolated feature — no family, no node
            members = [
                spectrum
                for spectrum in (self._resolve_node(spectra_by_id, n) for n in component)
                if spectrum is not None
            ]
            if len(members) < 2:
                continue
            # Sorting by the int feature_id gives the canonical key (members[0]) and a
            # deterministic emission order in one pass. connected_components yields
            # sets, so without this the key would depend on iteration order.
            members.sort(key=lambda s: s.feature_id)
            component_uri = AnalysisURIs.fbmn_component_uri(analysis, members[0].feature_id)
            g.add((featureset_uri, ENPKG.hasNetworkComponent, component_uri))
            if component_uri in self._emitted:
                continue
            self._emitted.add(component_uri)
            g.add((component_uri, RDF.type, EMI.FBMNComponent))
            self._set(component_uri, ENPKG.componentSize, len(members))
            for member in members:
                member_uri = AnalysisURIs.spectrum_uri(analysis, member)
                g.add((component_uri, ENPKG.hasComponentMember, member_uri))
                # EMI's own property runs feature -> component: its rdfs:domain is
                # emi:LCMSFeature, so the feature has to be the subject.
                g.add((member_uri, EMI.hasFBMNComponent, component_uri))

    def _add_ions(self, spectrum_uri: URIRef, spectrum: AnnotatedSpectrum) -> None:
        """Emit a named `enpkg:hasIon` product-ion node per (filtered) peak.

        Ion IRIs are ``{spectrum}/ion/{i}`` where ``i`` is the peak's index in the
        spectrum's full peak list — stable across re-serializations and visible in
        GraphDB (blank nodes are not surfaced by its browser / visual graph).
        """
        mz = np.asarray(spectrum.mz, dtype=float)
        intensities = np.asarray(spectrum.intensities, dtype=float)
        if mz.size == 0:
            return
        indices = np.arange(mz.size)  # keep original peak indices through filtering
        if self.min_relative_intensity > 0 and intensities.max() > 0:
            indices = indices[intensities[indices] >= self.min_relative_intensity * intensities.max()]
        if self.max_ions_per_spectrum is not None and indices.size > self.max_ions_per_spectrum:
            order = np.argsort(intensities[indices])[::-1][: self.max_ions_per_spectrum]
            indices = indices[order]
        g = self.graph
        # Provenance: the emitted peak list is a filtered subset, not the full one.
        if self.min_relative_intensity > 0:
            self._set(spectrum_uri, ENPKG.lowIntensityThreshold, self.min_relative_intensity)  # skos:exactMatch MS:1000629
        if self.max_ions_per_spectrum is not None:
            self._set(spectrum_uri, ENPKG.maxIonsPerSpectrum, self.max_ions_per_spectrum)
        for i in indices:
            ion = URIRef(f"{spectrum_uri}/ion/{int(i)}")
            g.add((spectrum_uri, ENPKG.hasIon, ion))
            # No rdf:type: PSI-MS's "product ion" (MS:1000342) is obsolete and has no
            # non-obsolete replacement; the node is identified by enpkg:hasIon plus the
            # m/z + intensity value properties below.
            self._set(ion, ENPKG.productIonMz, float(mz[i]))        # skos:exactMatch MS:1001225
            self._set(ion, ENPKG.productIonIntensity, float(intensities[i]))  # skos:exactMatch MS:1001226

    # ---------------------------------------------------------- internal helper
    def _same_as(self, subject: URIRef, target: URIRef) -> None:
        """Add ``owl:sameAs`` unless it would point the node at itself."""
        if target != subject:
            self.graph.add((subject, OWL.sameAs, target))


def serialize_to_turtle(analysis: Analysis, destination=None, **kwargs):
    """Serialize one `Analysis` to Turtle.

    Returns the Turtle string; if ``destination`` is given, writes there and
    returns ``None``. ``kwargs`` are forwarded to :class:`AnalysisSerializer`.
    """
    serializer = AnalysisSerializer(**kwargs)
    serializer.add_analysis(analysis)
    if destination is not None:
        serializer.graph.serialize(destination=str(destination), format="turtle")
        return None
    return serializer.graph.serialize(format="turtle")
