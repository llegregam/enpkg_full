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
The OTT match node, molecular network and (gated) product-ion layer are Phase 3.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import numpy as np
from rdflib import Graph, Literal, URIRef, BNode

from .namespaces import (
    RDF, RDFS, OWL,
    EMI, ENPKG, SOSA, MS, CHEMROF, NCBITAXON, NCBITAXON_PROP,
    PROV, DCTERMS, SKOS, EMI_RES,
    WD, INCHIKEY, PUBCHEM, GBIF, DOI,
)
from .uris import AnalysisURIs, CompoundURIs, OrganismURIs, _organism_uri
from ..data.analysis import Analysis
from ..data.annotated_spectra_class import AnnotatedSpectrum
from ..data.ms1_data_classes.adduct_class import ChemicalAdduct, AdductRecipe
from ..data.chemical_annotation import MS2ChemicalAnnotation
from ..data.lotus_class import Lotus

# Prefixes bound on the output graph (cosmetic — controls Turtle prefix display).
_PREFIXES = {
    "emi": EMI, "enpkg": ENPKG, "sosa": SOSA, "ms": MS, "chemrof": CHEMROF,
    "taxon": NCBITAXON, "ncbitaxon": NCBITAXON_PROP,
    "prov": PROV, "dcterms": DCTERMS, "skos": SKOS, "emi-res": EMI_RES,
    "wd": WD, "inchikey": INCHIKEY, "pubchem": PUBCHEM, "gbif": GBIF, "doi": DOI,
}

# Numeric-value predicates minted in our namespace, each declared a datatype
# property and skos:exactMatch-linked to the PSI-MS *class* it corresponds to.
# PSI-MS terms (MS_1003243, ...) are classes / cvParam concepts, not properties,
# so using them directly as predicates would pun a class as a property.
_PROPERTY_MAPPINGS = {
    "adductMass": ("1003243", "adduct ion mass"),
    "charge": ("1000041", "charge state"),
    "productIonMz": ("1001225", "product ion m/z"),
    "productIonIntensity": ("1001226", "product ion intensity"),
    "lowIntensityThreshold": ("1000629", "low intensity threshold"),
}

# Minted annotation subclasses of emi:StructuralAnnotation, used to tell apart the
# two annotation kinds that EMI otherwise unifies: MS1 adduct hypotheses and MS2
# spectral-library matches. Declared once per graph (label + subClassOf).
_CLASS_MAPPINGS = {
    "AdductAnnotation": ("Adduct annotation (MS1)", EMI.StructuralAnnotation),
    "SpectralAnnotation": ("Spectral library annotation (MS2)", EMI.StructuralAnnotation),
}

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
        include_network: bool = False,
        include_ions: bool = False,
        min_relative_intensity: float = 0.0,
        max_ions_per_spectrum: Optional[int] = None,
    ):
        self.graph = Graph()
        for prefix, namespace in _PREFIXES.items():
            self.graph.bind(prefix, namespace)
        self._declare_property_mappings()
        self._declare_class_mappings()
        # URIs of shared nodes already described (compounds, organisms, recipes,
        # references, 2D InChIKeys) so they are emitted once even across repeated
        # add_analysis calls.
        self._emitted: set[URIRef] = set()
        # Cap annotations per spectrum at the top-k best (default 5); a non-positive
        # value disables the cap (emit all).
        self.top_k_ms1 = top_k_ms1 if (top_k_ms1 is None or top_k_ms1 > 0) else None
        self.top_k_ms2 = top_k_ms2 if (top_k_ms2 is None or top_k_ms2 > 0) else None
        self.include_network = include_network
        self.include_ions = include_ions
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

    def _declare_property_mappings(self) -> None:
        """Declare each minted numeric predicate as a datatype property and link it
        to the PSI-MS class it means (skos:exactMatch) — so we query with a real
        property while keeping the PSI-MS semantics. Emitted once per graph."""
        for local, (ms_accession, label) in _PROPERTY_MAPPINGS.items():
            prop = ENPKG[local]
            self.graph.add((prop, RDF.type, OWL.DatatypeProperty))
            self.graph.add((prop, RDFS.label, Literal(label)))
            self.graph.add((prop, SKOS.exactMatch, MS[ms_accession]))

    def _declare_class_mappings(self) -> None:
        """Declare each minted annotation subclass as an owl:Class, link it under its
        EMI parent (rdfs:subClassOf) and label it. Lets consumers tell MS1 (adduct)
        from MS2 (spectral) annotations while both stay emi:StructuralAnnotation.
        Emitted once per graph."""
        for local, (label, parent) in _CLASS_MAPPINGS.items():
            cls = ENPKG[local]
            self.graph.add((cls, RDF.type, OWL.Class))
            self.graph.add((cls, RDFS.subClassOf, parent))
            self.graph.add((cls, RDFS.label, Literal(label)))

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
        polarity = (analysis.ionization_mode or "").lower()
        if polarity.startswith("pos"):
            g.add((uri, RDF.type, EMI.LCMSAnalysisPos))
        elif polarity.startswith("neg"):
            g.add((uri, RDF.type, EMI.LCMSAnalysisNeg))
        self._set(uri, DCTERMS.identifier, analysis.run_name)
        self._set(uri, EMI.hasMassiveDOI, getattr(analysis.metadata, "massive_id", None))

        g.add((uri, EMI.hasSample, self._add_sample(analysis)))
        g.add((uri, EMI.hasLCMSFeatureSet, self._add_featureset(analysis)))
        for match in (analysis.ott_matches or []):
            g.add((uri, ENPKG.hasOTTMatch, self._add_match(analysis, match)))
        if self.include_network:
            self._add_molecular_network(analysis)
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
        self._add_ranked_annotations(
            uri, spectrum.ms1_annotations,
            scores=(spectrum.ms1_pathway_scores, spectrum.ms1_superclass_scores, spectrum.ms1_class_scores),
            top_k=self.top_k_ms1,
            emit=lambda ann: self._add_chemical_adduct(analysis, spectrum, ann),
        )
        self._add_ranked_annotations(
            uri, spectrum.ms2_annotations,
            scores=(spectrum.ms2_pathway_scores, spectrum.ms2_superclass_scores, spectrum.ms2_class_scores),
            top_k=self.top_k_ms2,
            emit=lambda ann: self._add_ms2_annotation(analysis, spectrum, ann),
            fallback_key=lambda ann: ann.score,  # cosine, when NPC scores absent
        )
        if self.include_ions:
            self._add_ions(uri, spectrum)
        return uri

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
    ) -> None:
        """Emit a spectrum's annotations ranked by reweighted score, capped at
        top_k. Tags each kept node with enpkg:annotationRank (1=best) +
        enpkg:annotationScore. When the propagated NPC scores are absent (weights
        enhancer not run), falls back to ``fallback_key`` (e.g. MS2 cosine) for
        ranking; if that's also absent, keeps stored order. The cap is always
        applied so top_k means top_k, ranked or not."""
        if not annotations:
            return
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
        for rank, (score, annotation) in enumerate(ranked, start=1):
            annotation_uri = emit(annotation)
            self.graph.add((spectrum_uri, EMI.hasAnnotation, annotation_uri))
            self._set(annotation_uri, ENPKG.annotationRank, rank)
            self._set(annotation_uri, ENPKG.annotationScore, score)

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
        self._set(uri, EMI.hasAdduct, self._format_adduct(adduct.recipe))  # "[M+H]+" form (EMI property)
        g.add((uri, ENPKG.hasRecipe, self._add_recipe(adduct.recipe)))
        for lotus in adduct.lotus:
            g.add((uri, EMI.hasChemicalStructure, self._add_compound(lotus)))
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

    def _add_molecular_network(self, analysis: Analysis) -> None:
        network = analysis.molecular_network
        if network is None:
            return
        g, run = self.graph, analysis.run_name
        for u, v, data in network.edges(data=True):
            edge = BNode()
            g.add((edge, RDF.type, EMI.LFpair))
            g.add((edge, EMI.hasFirstMember, EMI_RES[f"spectrum/{run}/{int(u)}"]))
            g.add((edge, EMI.hasSecondMember, EMI_RES[f"spectrum/{run}/{int(v)}"]))
            # matchms SimilarityNetwork stores the cosine under "weight".
            score = data.get("weight", data.get("ModifiedCosine_score"))
            self._set(edge, EMI.hasCosine, float(score) if score is not None else None)

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
