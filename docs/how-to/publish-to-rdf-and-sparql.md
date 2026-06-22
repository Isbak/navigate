# Publish to RDF and SPARQL

**Goal:** project your **approved** knowledge base into RDF and query it with
SPARQL — either against an in-memory projection or a live Apache Jena Fuseki
triplestore.

This is strictly a **projection layer**: SQLite stays the system of record, and
only `APPROVED` objects and `APPROVED` relationships (with both endpoints
approved) ever cross the boundary — nothing `PROPOSED`, `REVIEWED`, or `REJECTED`.

## Prerequisites

- A graph with **approved** objects/relationships — see
  [Build a knowledge graph](build-a-knowledge-graph.md) and
  [Govern your knowledge](govern-your-knowledge.md).

## 1. Export RDF

`catalog rdf-export` writes four files under `exports/rdf/` (Turtle by default;
`--format json-ld` and `--format nt` also supported):

```bash
catalog rdf-export [--out DIR] [--format turtle|json-ld|nt]
catalog rdf-validate [--out DIR]    # re-parse each exported file with rdflib
catalog rdf-stats                    # objects / relationships / evidence that would export (APPROVED only)
```

The four files are `ontology.ttl` (classes + predicates + provenance
vocabulary), `knowledge.ttl` (one resource per approved object),
`relationships.ttl` (approved object-to-object triples), and `provenance.ttl`
(named `kg:Evidence` resources). Keeping them separate keeps the export
forward-compatible with named graphs in a quad store.

## 2. Start a local Fuseki (optional)

A `docker-compose.yml` is included. It starts an update-enabled in-memory dataset
at `http://localhost:3030/knowledge-atlas`, matching the default
`config/jena.yml` endpoint.

```bash
docker compose up -d fuseki
catalog rdf-export
catalog fuseki-load                  # validate, then upload ontology → knowledge → relationships → provenance
```

To wipe the dataset:

```bash
catalog fuseki-clear                 # CLEAR ALL triples
```

Fuseki connection details live in `config/jena.yml` (`fuseki.endpoint`,
`fuseki.dataset`); a missing file falls back to
`http://localhost:3030/knowledge-atlas`.

## 3. Run SPARQL queries

`queries/` holds ready-to-run `.rq` examples — `all_capabilities.rq`,
`all_decisions.rq`, `all_relationships.rq`, `related_capabilities.rq`, and more.
After `catalog fuseki-load`, the headline query returns the approved
capabilities:

```sparql
PREFIX kg: <https://knowledge-atlas.local/kg/>
SELECT ?capability WHERE { ?capability a kg:Capability . }
```

> You can also run these queries without Fuseki via the in-memory projection —
> see [Explore the knowledge graph](explore-the-knowledge-graph.md)
> (`catalog graph query <name>`).

## Related

- Navigate the same data without a server: [Explore the knowledge graph](explore-the-knowledge-graph.md)
- Compliance SPARQL (`queries/compliance_*.rq`): [docs/compliance.md](../compliance.md)

---

## How it works (URI strategy)

Every resource lives under the base namespace
`https://knowledge-atlas.local/kg/`. Classes and predicates are prefixed names
(`kg:Capability`, `kg:supports`). Instances use a stable, per-type path derived
from the object's stable id:

```
https://knowledge-atlas.local/kg/capability/release_governance
https://knowledge-atlas.local/kg/decision/launchpad_model
https://knowledge-atlas.local/kg/platform/salesforce
```

Ids are lowercase, snake_case, and deterministic — the same object always yields
the same URI, and collision-suffixed ids stay unique. No random ids are minted,
so the URIs are stable across re-exports.
