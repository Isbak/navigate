"""RDF export and Apache Jena Fuseki integration (Prompt #7).

This package is a *projection layer* over the knowledge base. It does not own any
data: SQLite remains the system of record and APPROVED knowledge objects remain
the authoritative source. Fuseki merely receives exported RDF and serves it as a
query layer.

What this package does
----------------------
* Maps each approved knowledge object to a stable URI (``namespaces``).
* Defines the ontology - classes and predicates (``ontology``).
* Projects approved objects, relationships, and evidence into RDF graphs and
  writes Turtle / JSON-LD / N-Triples files (``export``).
* Loads those files into Fuseki via SPARQL Update (``fuseki``, ``config``).

What this package does NOT do (explicitly out of scope)
-------------------------------------------------------
No GraphRAG, no chat interface, no retrieval, no vector search, no embeddings,
no LLM querying. It produces RDF and loads it into Jena - nothing more.
"""

from __future__ import annotations

from .config import FusekiConfig, load_jena_config
from .export import (
    DEFAULT_OUT_DIR,
    FORMATS,
    build_graphs,
    build_knowledge_graph,
    build_provenance_graph,
    build_relationships_graph,
    export_rdf,
    rdf_stats,
    validate_rdf,
)
from .fuseki import FusekiError, clear_dataset, fuseki_load, upload_graph
from .namespaces import (
    BASE,
    KG,
    class_uri,
    evidence_uri,
    object_uri,
    predicate_uri,
)
from .ontology import build_ontology_graph

__all__ = [
    # namespaces
    "BASE",
    "KG",
    "class_uri",
    "evidence_uri",
    "object_uri",
    "predicate_uri",
    # ontology
    "build_ontology_graph",
    # export
    "DEFAULT_OUT_DIR",
    "FORMATS",
    "build_graphs",
    "build_knowledge_graph",
    "build_relationships_graph",
    "build_provenance_graph",
    "export_rdf",
    "rdf_stats",
    "validate_rdf",
    # config
    "FusekiConfig",
    "load_jena_config",
    # fuseki
    "FusekiError",
    "clear_dataset",
    "fuseki_load",
    "upload_graph",
]
