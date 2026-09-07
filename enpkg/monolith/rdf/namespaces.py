"""RDF namespaces for the ENPKG knowledge-graph serializer.

Two kinds of namespaces:
  * **Vocabulary** — classes & predicates we *reuse* (EMI, SOSA, MS/PSI-MS,
    ChemROF, NCBITaxon, PROV, DCTERMS, SKOS, OWL/RDF/RDFS) or *mint* ourselves
    (ENPKG).
  * **Resource / identifier** — IRIs that *name individual entities*, either
    minted by us (EMI_RES) or external identifier authorities used as objects /
    ``owl:sameAs`` targets (WD, INCHIKEY, PUBCHEM, GBIF, NCBITAXON, DOI).

The full entity -> term mapping lives in ``docs/RDF_DATA_MODEL_mapped.md``.
"""

from rdflib import Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD  # re-exported for one-stop import

# --- Vocabulary: classes & predicates ---------------------------------------
EMI       = Namespace("https://w3id.org/emi#")                # Earth Metabolome Ontology (backbone)
ENPKG     = Namespace("https://w3id.org/enpkg#")              # our minted terms — TODO: confirm an IRI we control
NPC       = Namespace("https://w3id.org/emi/npc#")            # NPClassifier terms, vendored inside EMI-vocab.owl
SOSA      = Namespace("http://www.w3.org/ns/sosa/")           # samples / observations
MS        = Namespace("http://purl.obolibrary.org/obo/MS_")   # PSI-MS: adduct family, product ions, charge
CHEMROF   = Namespace("https://w3id.org/chemrof/")            # ChEBI structural annotation properties
NCBITAXON = Namespace("http://purl.obolibrary.org/obo/NCBITaxon_")   # taxon + rank classes
NCBITAXON_PROP = Namespace("http://purl.obolibrary.org/obo/ncbitaxon#")  # has_rank
PROV      = Namespace("http://www.w3.org/ns/prov#")           # provenance / derivation
DCTERMS   = Namespace("http://purl.org/dc/terms/")            # identifier, source, bibliographicCitation
SKOS      = Namespace("http://www.w3.org/2004/02/skos/core#")  # prefLabel / altLabel
# CHEMONT = Namespace(...)  # TODO: ClassyFire ChemOnt IRI for `chemontid` owl:sameAs — verify exact form

# --- Resources: instance IRIs we mint ---------------------------------------
EMI_RES   = Namespace("https://w3id.org/emi/resource/")       # minted entity URIs (see rdf/uris.py)

# --- External identifier authorities (object IRIs / owl:sameAs targets) ------
WD        = Namespace("http://www.wikidata.org/entity/")
INCHIKEY  = Namespace("https://identifiers.org/inchikey/")    # canonical, resolvable resolver
PUBCHEM   = Namespace("https://identifiers.org/pubchem.compound/")
GBIF      = Namespace("https://www.gbif.org/species/")
DOI       = Namespace("https://doi.org/")
MASSIVE   = Namespace("https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=")  # emi:hasMassiveDOI target
# NCBITAXON (above) also serves as the owl:sameAs target for organism NCBI ids.

__all__ = [
    "RDF", "RDFS", "OWL", "XSD",
    "EMI", "ENPKG", "NPC", "SOSA", "MS", "CHEMROF", "NCBITAXON", "NCBITAXON_PROP",
    "PROV", "DCTERMS", "SKOS",
    "EMI_RES",
    "WD", "INCHIKEY", "PUBCHEM", "GBIF", "DOI", "MASSIVE",
]
