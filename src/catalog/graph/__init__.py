"""Knowledge Explorer and SPARQL query layer (Prompt #8).

A query and exploration layer over the **approved** knowledge graph. It lets a
user discover, navigate, inspect, and validate knowledge using only SPARQL,
graph algorithms, and graph metrics - explicitly *before* (and without) any
GraphRAG, LLM, vector search, or embeddings.

    SQLite (system of record) -> RDF projection -> GraphClient (SPARQL)
                                                 -> NetworkX (algorithms)

* :class:`~catalog.graph.client.GraphClient` - execute / load / list / save
  SPARQL queries against a local rdflib graph or a live Fuseki endpoint.
* :mod:`catalog.graph.network` - NetworkX view: paths, neighbours, impact,
  centrality.
* :mod:`catalog.graph.health` / :mod:`catalog.graph.domains` - validation and
  domain analysis.
* :mod:`catalog.graph.export` - GEXF / GraphML / JSON + a visualization bundle.
"""

from __future__ import annotations

from .client import GraphClient, QueryError

__all__ = ["GraphClient", "QueryError"]
