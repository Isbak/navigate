# Explore the knowledge graph

**Goal:** search, navigate, validate, and analyze your approved knowledge graph
using only SPARQL and graph algorithms — no LLM, no vectors, no embeddings. This
proves the graph's usefulness on its own before any AI layer.

## Prerequisites

- A graph with **approved** objects and relationships — see
  [Build a knowledge graph](build-a-knowledge-graph.md). Only `APPROVED` items
  appear here.

By default every `catalog graph` command rebuilds the approved RDF projection in
memory straight from SQLite and runs real SPARQL against it with rdflib — nothing
here needs a running server. Add `--fuseki` to route the same SPARQL to a live
Apache Jena Fuseki endpoint (`config/jena.yml`) instead; see
[Publish to RDF and SPARQL](publish-to-rdf-and-sparql.md).

## Explore

```bash
catalog graph search "release"        # search labels/descriptions; matches + relationship counts
catalog graph show <id>               # object detail: type, description, confidence, connections, evidence
catalog graph neighbors <id>          # connected objects grouped by relationship type
catalog graph path <id1> <id2>        # shortest path between two objects
catalog graph impact <id>             # what a change may affect, grouped by type
```

## Validate and analyze

```bash
catalog graph health                  # objects/relationships missing evidence, low confidence, duplicates, disconnected subgraphs
catalog graph domains                 # knowledge domains by object type, with the most central concept in each
catalog graph metrics                 # NetworkX centrality, components, density, clusters
```

`catalog graph metrics` also writes the visualization bundle
`exports/graph/{nodes,edges,metrics}.json`.

## Run saved SPARQL queries

The query library lives in `queries/` as `.rq` files
(`all_capabilities`, `all_decisions`, `all_teams`, `all_platforms`,
`related_objects`, `top_relationships`, `object_dependencies`,
`knowledge_domains`, `evidence_for_object`, and more).

```bash
catalog graph query                   # list the saved queries
catalog graph query all_capabilities  # run one
```

## Export for other tools

```bash
catalog graph export-gexf             # GEXF for Gephi
catalog graph export-graphml          # GraphML for Neo4j / yEd / Cytoscape
catalog graph export-json             # node-link JSON for NetworkX / D3
```

Files are written under `exports/graph/` (override with `--out DIR`).

## Explore interactively

```bash
catalog graph explore                 # read-only Rich REPL: search, show, neighbors, evidence
```

## Next step

Ready for natural-language questions with reasoning and citations? See
[Ask questions with GraphRAG](ask-questions-with-graphrag.md).

---

## How it works

The `catalog.graph` package is a query and exploration layer over the approved
knowledge graph:

```
SQLite (system of record) → RDF projection → GraphClient (SPARQL)
                                           → NetworkX (algorithms)
```

`GraphClient` (`catalog.graph.client`) executes SPARQL and manages the on-disk
query library (`execute_query()`, `load_query()`, `list_queries()`,
`save_query()`). With this layer you can answer — using only the graph — what
capabilities connect to Release Governance, what decisions affect Salesforce,
what the most central concepts are, what evidence supports a capability, and the
shortest path between a team and a capability.
