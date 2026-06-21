# Knowledge Catalog

A local-first professional knowledge catalog that indexes selected folders in place. It stores document metadata in SQLite, detects what changed between scans, extracts text and hyperlinks into a local cache, discovers and classifies how documents link to internal and external knowledge systems, and can optionally watch files for changes.

## Design principles

- Source documents are never moved, renamed, or modified.
- The catalog is an index, not a document store.
- Only extracted text and metadata are cached under `cache/`.
- The SQLite database remains local under `data/` by default.
- The module layout is intentionally simple so future modules such as `llm_enrichment.py`, `rdf_export.py`, and `fuseki_loader.py` can be added without changing scanner, database, or CLI boundaries.

## Architecture

The scanner is built as a small pipeline so that discovery stays reliable and
future processing can be added without touching it:

```
scanner  ->  artifact queue  ->  database
                   |
                   +--> scan event bus --> subscribers (text/link extraction, ...)
```

- **scanner** (`scanner.py`) walks the configured folders, hashes each file, and
  classifies it against the existing index (new / changed / unchanged / deleted /
  duplicate).
- **artifact queue** (`queue.py`) hands records from discovery to a database
  writer running on its own thread.
- **database** (`db.py`) upserts artifact rows and records per-run statistics.
- **scan event bus** (`events.py`) publishes one event per processed artifact.
  Extractors subscribe to the statuses they care about; the bundled
  `extraction.py` subscribes to `RAW`/`CHANGED` events to cache text and raw links.

### Extraction vs. link discovery

Link handling is deliberately split into two stages that never mix concerns:

```
extraction  ->  cache/<artifact_id>/links.json     (raw links, no database)
link discovery  ->  reads links.json, writes SQLite (normalize + classify)
```

- **extraction** (`extraction.py`) writes, per artifact, `extracted.txt`,
  `links.json` (raw `{raw_url, anchor_text}` pairs), and `metadata.json`. It
  never touches the database.
- **link discovery** (`links/`) reads those `links.json` files, normalizes and
  classifies each URL with deterministic rules, and persists the results to
  SQLite. No source documents are read, and there is no LLM, semantic
  classification, or RDF generation in this phase.

The `links/` package is organized for extension:

- `normalizer.py` — deterministic URL normalization (trim, trailing
  punctuation, lowercase scheme/host, tracking-parameter removal, fragment and
  `mailto:`/`file://` handling).
- `classifier.py` — pattern-based `target_system`, `target_type`, and
  `link_kind` classification.
- `config.py` — loads user-defined internal domains and system patterns from
  `config/link_patterns.yml`.
- `repository.py` — all SQL for the `links` and `link_scan_runs` tables,
  including deduplication and stale handling.
- `service.py` — orchestrates a discovery run and records its statistics.

## Supported files

The scanner recursively indexes:

- `.docx`
- `.pptx`
- `.xlsx`
- `.pdf`
- `.md`
- `.txt`

## Installation

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Configuration

Edit `config/sources.yml` to choose local folders to index:

```yaml
sources:
  - path: "~/Documents"
    source_system: "local_laptop"
  - path: "~/Desktop"
    source_system: "local_laptop"
  - path: "~/OneDrive"
    source_system: "onedrive_sync"

exclude:
  - "**/.git/**"
  - "**/node_modules/**"
  - "**/~$*"
  - "**/.DS_Store"
```

Each source path is expanded with `~` support. Missing source directories are skipped with a warning.

## Usage

Initialize the local SQLite database:

```bash
catalog init-db
```

Scan configured folders:

```bash
catalog scan
```

The scan reports a summary and stores it for later:

```
Files scanned: 823
New files: 18
Modified files: 7
Deleted files: 2
Duplicates: 31
```

Watch configured folders and rescan changed supported files after a debounce delay:

```bash
catalog watch
```

Show catalog statistics:

```bash
catalog stats
```

Show duplicates detected by identical SHA-256 content hashes:

```bash
catalog show-duplicates
```

(Re)build the extraction cache for every indexed document:

```bash
catalog extract                                  # fast text extraction (default)
catalog extract --mode high-quality              # vision pass for hard PDF pages
catalog extract --path-glob '**/*standard*.pdf'  # only matching files
catalog extract --artifact-id doc_abc123         # only these ids (repeatable)
```

Extraction has two modes (configured in `config/extraction.yml`, overridable with
`--mode`):

- **fast** — text-only extraction. Offline, no API calls. Good default.
- **high-quality** — for PDFs, the fast pass runs first and only the pages it
  cannot read well (scanned pages, figures, and equations rendered as images)
  are rendered to an image and transcribed to Markdown + LaTeX by the Claude
  provider from `config/llm.yml`. This needs network access and
  `ANTHROPIC_API_KEY`; cost stays proportional to the suspect pages.

Re-extracting a file overwrites its `extracted.txt`, so a subsequent
`catalog classify --artifact-id <id>` (repeatable) reprocesses exactly those
files.

Discover, normalize, and classify links from the extraction cache:

```bash
catalog discover-links            # all cached artifacts
catalog discover-links --artifact-id doc_abc123  # a single artifact
```

A discovery run reports a summary and stores it in `link_scan_runs`:

```
Link discovery complete:
Artifacts processed: 318
Links found: 1,842
New links: 212
Updated links: 1,599
Stale links: 31
Errors: 3

Target systems:
sharepoint: 911
azure_devops: 277
confluence: 188
teams: 142
external_web: 91
unknown: 233
```

Show link statistics (totals, breakdowns, and top references):

```bash
catalog link-stats
```

Show links from one artifact, or filtered by target system:

```bash
catalog show-links --artifact-id doc_abc123
catalog show-links --system sharepoint
```

Show links that were not found in the latest extraction:

```bash
catalog show-stale-links
```

Export every link to `exports/links.csv`:

```bash
catalog export-links-csv
```

A typical end-to-end run:

```bash
catalog scan
catalog extract
catalog discover-links
catalog link-stats
catalog classify
catalog classification-stats
catalog consolidate
catalog knowledge-stats
```

All commands accept alternate paths:

```bash
catalog --config config/sources.yml --db data/catalog.sqlite --cache cache --link-config config/link_patterns.yml scan
```

## Scan status

Every artifact carries a `scan_status` describing what the most recent scan
found:

- `RAW` — newly discovered file, never seen before
- `CHANGED` — known path whose content hash changed
- `UNCHANGED` — known path with identical content
- `DELETED` — previously indexed path no longer present on disk
- `DUPLICATE` — content hash already seen at another path during the scan

## Data model

`artifacts` contains stable document metadata, keyed by source `path`:

- `path` — absolute source path (primary key; never modified)
- `id` — content-addressed identity `doc_<first_12_sha256_chars>`. Byte-identical
  files share the same `id`, which is how duplicates are detected.
- `filename`, `file_type`, `size_bytes`
- filesystem timestamps (`created_at`, `modified_at`)
- `sha256`
- `source_system`
- `scan_status`
- `first_seen_at`, `last_scanned_at`

`scan_runs` stores statistics for each scan: files scanned, new, changed,
unchanged, duplicate, and deleted counts, plus start/finish timestamps. `catalog
stats` reports the latest run.

`links` contains normalized, classified hyperlinks produced by the discovery
layer:

- `source_artifact_id` — the content-addressed id (`doc_<sha>`) of the source
  document. (This references `artifacts.id`, which is intentionally **not**
  unique because byte-identical duplicates share an id; SQLite can only enforce
  a foreign key against a unique parent, so the relationship is modeled with an
  index and maintained by the discovery service rather than a hard constraint.)
- `raw_url` — the original URL exactly as extracted
- `normalized_url` — the deduplication-friendly normalized form
- `anchor_text` — optional link text
- `target_system`, `target_type`, `link_kind` — classifications
- `discovered_at`, `last_seen_at`
- `status` — `ACTIVE` or `STALE`

A link is considered a duplicate when `source_artifact_id + normalized_url +
anchor_text` already exists; re-running discovery refreshes `last_seen_at`
instead of inserting. Links no longer present in the latest extraction of an
artifact are flagged `STALE` (never deleted), and a reappearing link returns to
`ACTIVE`.

`link_scan_runs` stores per-run statistics: artifacts processed, links found,
new, updated, removed (stale), and errors, plus start/finish timestamps.

> Note: discovery is driven entirely by the cache. Because ids are
> content-addressed, editing a document yields a *new* artifact id and a new
> cache entry; pruning superseded cache directories is a cache-lifecycle concern
> left to a future phase.

## Link classification

Classification is deterministic, pattern-based, and uses no LLM.

`target_system`:

- `sharepoint`
- `onedrive`
- `confluence`
- `jira`
- `azure_devops`
- `github`
- `teams`
- `email`
- `local_file`
- `external_web`
- `unknown`

`target_type` (best effort): `document`, `wiki_page`, `work_item`,
`repository`, `pull_request`, `meeting`, `message`, `channel`,
`email_address`, `folder`, `unknown`.

`link_kind`: `internal` (company knowledge systems), `external` (public web),
`local` (file links), `email`, or `unknown`.

### Configuring internal domains

`config/link_patterns.yml` (optional) lets you declare which domains count as
internal and extend system matching:

```yaml
internal_domains:
  - "company.sharepoint.com"
  - "company.atlassian.net"
  - "dev.azure.com/company"
  - "wiki.company.com"

systems:
  sharepoint:
    domains:
      - "sharepoint.com"
  confluence:
    domains:
      - "atlassian.net"
      - "wiki.company.com"
```

When the file is absent, the built-in patterns are used. `github.com` is treated
as `external` unless one of its URLs matches a configured internal domain.

## Semantic classification and knowledge discovery

The semantic layer (`catalog/semantic/`) uses an LLM to *analyze* extracted
documents and *propose* structured knowledge. It is the next stage of the
pipeline, fed entirely by the extraction cache and SQLite catalog:

```
filesystem -> scanner -> SQLite catalog -> document cache -> link discovery
                                                  |
                                                  +--> semantic classification
```

This phase **does not create facts.** It creates classifications, observations,
hypotheses, and candidate relationships — every one carrying a confidence score,
its provenance, and a `review_status` of `NEW` for a human to approve later.
There is no RDF, no Jena, and no GraphRAG here.

### LLM provider abstraction

The service depends only on `BaseLLMProvider`, so it is agnostic to the backend.
Three providers ship today and new ones are a one-line registration plus a small
subclass:

- `OllamaProvider` — talks to a local Ollama server (fully offline).
- `ClaudeProvider` — talks to Anthropic's Claude Messages API.
- `OpenAIProvider` — talks to the OpenAI Chat Completions API.

All providers use only the standard library (no vendor SDK dependency). Keep
shareable, non-secret provider settings in `config/llm.yml`; put real API keys in
your shell environment or an ignored `.env` copied from `.env.example`:

```yaml
provider: claude

ollama:
  model: qwen3:14b
  host: http://localhost:11434

claude:
  model: claude-sonnet-4-5
  api_key_env: ANTHROPIC_API_KEY  # value is read from env/.env, not YAML
  prompt_cache: true              # cache the constant system prompt

openai:
  model: gpt-5.5
  api_key_env: OPENAI_API_KEY     # value is read from env/.env, not YAML

max_input_chars: 12000      # size of one chunk sent to the model
chunk_overlap: 500          # overlap between consecutive chunks
max_chunks: 20              # cap on chunks per document (bounds cost)

routing:                    # adaptive model routing (see docs/llm-optimization.md)
  enabled: true
  fast_model: claude-haiku-4-5
  deep_model: claude-sonnet-4-5
```

Long documents are split into chunks of `max_input_chars` characters (with
`chunk_overlap` overlap, up to `max_chunks` chunks) and classified chunk by
chunk; the per-chunk results are merged so equations and content past the head
of the document are no longer lost. Simple documents are routed to a fast model
and complex ones (or low-confidence results) to a deep model — see
[Adaptive LLM optimization](#adaptive-llm-optimization).

### What it determines, per document

- **document_type** — one of Governance, Strategy, Architecture, Roadmap,
  Project, Meeting Notes, Workshop, Presentation, Budget, Report, Requirements,
  Technical Design, Operating Model, Training, Other (with a confidence).
- **domains** — one or more business/technology areas (Test & Release,
  Architecture, SAP, Data, Finance, HR, ...), each scored.
- **summaries** — a short summary (≤100 words) and a long summary (≤500 words).
- **candidate entities** — Capability, Initiative, Team, Product, Platform,
  Process, Technology, Concept, Decision, or Risk.
- **candidate capabilities** — business capabilities discussed (Release
  Management, Change Management, Incident Management, ...).
- **candidate decisions** — decisions the document appears to make, with a
  supporting quote. Never marked approved.
- **candidate risks** — risks the document implies, with a supporting quote.
- **candidate relationships** — `subject predicate object` triples using the
  predicates `supports`, `depends_on`, `implements`, `mentions`, `references`,
  `affects`, `owned_by`, `related_to`.

### Storage tiers and provenance

Knowledge is separated into tiers via a `knowledge_type` column. This phase only
ever writes `OBSERVATION` (read directly off the document) and `HYPOTHESIS`
(an inferred claim) — **never `FACT`**. Every semantic row records its
provenance: `artifact_id`, `model`, `created_at` (timestamp), `confidence`, and
`supporting_text`. Every row starts with `review_status = NEW`; the review
workflow (`NEW → REVIEWED → APPROVED → REJECTED`) is left for a future phase.

### Incremental processing

A document is (re)classified only when its extraction changed (its content hash
differs from the stored `source_hash`), its classification is missing, or
reclassification is forced. Unchanged documents are skipped, so re-runs are
cheap. Reclassifying replaces a document's prior semantic rows atomically.

### Commands

```bash
catalog classify                          # classify all changed/new documents
catalog classify --artifact-id doc_abc123 # one document
catalog classify --force                  # reclassify everything

catalog classification-stats              # document types + knowledge-discovery analytics
catalog show-summary --artifact-id doc_abc123
catalog show-decisions [--min-confidence 0.7]
catalog show-risks
catalog show-capabilities
catalog show-relationships
```

`classification-stats` answers the knowledge-discovery questions without reading
every document: what kinds of documents exist, the top domains and capabilities,
the most common technologies and most referenced concepts, the most common
decision themes, the risks recurring across multiple documents, and which
concepts connect multiple domains.

The active provider is selected with `--llm-config` (default `config/llm.yml`).

### Semantic data model

- `document_classifications` — one row per document: `document_type`,
  `type_confidence`, `domains` (JSON), `short_summary`, `long_summary`,
  `knowledge_type`, `review_status`, `model`, `source_hash`, `created_at`.
- `candidate_entities`, `candidate_capabilities`, `candidate_decisions`,
  `candidate_risks`, `candidate_relationships` — the proposed knowledge, each
  with confidence, supporting text, knowledge type, review status, and
  provenance.
- `classification_runs` — per-run statistics (documents processed/skipped,
  errors, model, timestamps).

## Knowledge consolidation and graph foundation

The knowledge layer (`catalog/knowledge/`) converges the *document-level*
proposals of the semantic layer into reusable, cross-document **knowledge
objects**. It is the final stage of the pipeline:

```
filesystem -> scanner -> SQLite catalog -> document cache -> link discovery
                                                  |
                                                  +--> semantic classification
                                                            |
                                                            +--> knowledge consolidation
```

Three documents that each talk about "Release Governance" / "Release Governance
Model" / "Release governance" collapse into a single
`capability_release_governance` object with traceable evidence and typed
relationships to other objects:

```
Document A: "Release Governance"
Document B: "Release Governance"          ─┐
Document C: "Release Governance Model"     ├─►  Knowledge Object: Release Governance
Document D: "Release governance"          ─┘
```

This phase is a **knowledge layer**, not GraphRAG, not Jena, not RDF, and not
SPARQL — and it builds no visualization UI. It is the *graph-ready foundation*:
every object has a stable, URI-ready id (`platform_salesforce`,
`decision_launchpad_model`) that a future RDF mapping can adopt as a resource
identifier without renaming anything.

### Entity resolution

Consolidation gathers every entity proposal from the semantic `candidate_*`
tables and groups the ones that refer to the same thing using, in order of
strength:

- **case + punctuation + whitespace normalization** — collapses "Release
  governance" into "Release Governance".
- **fuzzy matching** — a blend of token-set similarity and a character-trigram
  Dice coefficient, with a containment boost so "Release Governance Model" merges
  into "Release Governance".
- **LLM-assisted merge suggestions** *(optional)* — with `--use-llm`, borderline
  pairs (above the review threshold but below the auto-merge threshold) are put
  to the model as a yes/no question instead of guessed.

Every cluster records its **merge confidence** (the cohesion that held it
together). Pairs that are similar but below the auto-merge threshold are **not**
merged; they surface as *duplicate candidates* for a human to review. Objects
that share a name but were typed differently across documents (e.g. a `Concept`
and a `Capability` both called "Release Governance") are surfaced separately by
`review-candidates` as *same name, different type* — never auto-merged, since
picking the right type is a review decision.

Low-confidence, one-off proposals are dropped before clustering by a
configurable **noise floor** (`min_mention_confidence`, default `0.3`, overridable
with `consolidate --min-confidence`), so the long tail of weak mentions does not
each become its own object. See [`docs/classification-audit.md`](docs/classification-audit.md)
for the full audit of classification and knowledge discovery.

### Evidence, relationships, and review

- **Every object is traceable.** No knowledge object exists without at least one
  `knowledge_evidence` row (a supporting quote, with optional page/slide
  locators for the future).
- **Relationships** between objects (`Platform implements Capability`,
  `Initiative depends_on Capability`, `Risk affects Platform`, ...) are resolved
  from the per-document candidate relationships and typed with the same
  predicate vocabulary as the semantic layer.
- **Review workflow.** Objects and relationships start as `PROPOSED` and move
  through `REVIEWED → APPROVED → REJECTED`. **Only `APPROVED` items are
  trusted.** A normal `consolidate` rebuilds the derived data from scratch but —
  because ids are stable — re-applies prior human decisions; `--force` discards
  them.

### Knowledge scoring

Each object's confidence blends five signals: the number of distinct documents,
the number of mentions, relationship consistency (the fraction of its
relationships not rejected on review), the average LLM confidence of its
mentions, and review history (approval nudges it up, rejection drives it down).
Breadth beats repetition — something asserted once in 27 documents outranks
something repeated 27 times in one.

### Commands

```bash
catalog consolidate                # build knowledge objects from semantic data
catalog consolidate --force        # rebuild, discarding prior review decisions
catalog consolidate --use-llm      # use the LLM for borderline merge suggestions
catalog consolidate --all-sources  # ignore the source-folder scope (legacy)
catalog consolidate --min-confidence 0.5  # raise the noise floor (default 0.3)

catalog clean-source --path PATH   # permanently purge all material for a file/folder
catalog clean-source --path PATH --no-reconsolidate   # purge without rebuilding

catalog knowledge-stats            # top capabilities/concepts/technologies, most
                                   # connected/mentioned, conflicts, duplicates
catalog knowledge-growth           # growth trend (new + cumulative) by month
catalog knowledge-growth --interval week --limit 8
catalog show-object capability_release_governance
catalog search-knowledge "release"
catalog review-candidates          # PROPOSED objects/relationships + duplicates
catalog approve-object <id>
catalog reject-object <id>
catalog approve-relationship <id>  # relationship id from review-candidates / show-object
catalog reject-relationship <id>
catalog export-graph-json          # writes exports/graph/{nodes,edges}.json
```

After `catalog consolidate`, the success-criteria questions are answerable from
consolidated objects rather than individual documents: *what are the core
capabilities, which decisions are repeatedly referenced, and which concepts
connect multiple domains.*

#### Source-folder scope

Consolidation only considers documents that currently live under a configured
source folder in `config/sources.yml` (curated standard imports, which have no
file path, always count). Drop a folder from the config and re-run
`catalog consolidate`: objects sourced solely from that folder disappear from the
knowledge graph, while their raw `candidate_*` rows stay in the database — so
re-adding the path and re-consolidating brings them back. `--all-sources` opts
out and consolidates every classified document.

To permanently remove material instead of just descoping it, use
`catalog clean-source --path <file-or-folder>`: it deletes the artifact rows,
semantic candidates, classification, links, and extraction cache for everything
under that path, then re-consolidates (pass `--no-reconsolidate` to skip). A
byte-identical duplicate that still lives under another folder is preserved.

### Knowledge data model

- `knowledge_objects` — the consolidated objects: stable URI-ready `id`, `name`,
  `object_type` (Capability, Initiative, Technology, Platform, Team, Product,
  Concept, Decision, Risk, Process), `description`, `canonical_name`,
  `confidence`, `status`, `merge_confidence`, `created_at`, `updated_at`.
- `knowledge_mentions` — every (object, document) occurrence with its confidence
  and source text.
- `knowledge_evidence` — traceable quotes with optional `page_number` /
  `slide_number`; the invariant is that no object exists without evidence.
- `knowledge_relationships` — `source_object predicate target_object` triples
  with confidence, evidence (JSON quotes), and a `review_status`.
- `knowledge_reviews` — the audit trail of review actions, used by scoring.

Unlike the semantic tables, a `knowledge_object_id` references
`knowledge_objects.id` — a real, stable primary key — so genuine foreign keys
with `ON DELETE CASCADE` are used. Everything here is fully regenerable from the
semantic tables via `catalog consolidate`.

### Graph export

`catalog export-graph-json` writes `nodes.json` and `edges.json` under
`exports/graph/` for a **future** visualization (none is built in this phase).
Nodes are the knowledge objects; edges are the relationships. `REJECTED` objects
and relationships are excluded, while `PROPOSED` ones are included with their
status so a viewer can distinguish trusted from candidate links. The stable
object ids are used verbatim as node ids — exactly what a later RDF mapping will
key on.

## RDF export and Apache Jena integration

The `catalog.rdf` package projects the **approved** knowledge base into RDF and
loads it into [Apache Jena Fuseki](https://jena.apache.org/documentation/fuseki2/).
It is strictly a **projection layer**: SQLite remains the system of record,
approved knowledge objects remain authoritative, and Fuseki only ever *receives*
exported data to serve as a query layer. Only `APPROVED` objects and `APPROVED`
relationships (with both endpoints approved) cross the boundary — nothing
`PROPOSED`, `REVIEWED`, or `REJECTED` is ever exported.

### URI strategy

Every resource lives under the base namespace `https://knowledge-atlas.local/kg/`.
Classes and predicates are prefixed names (`kg:Capability`, `kg:supports`).
Instances use a stable, per-type path derived from the object's stable id:

```
https://knowledge-atlas.local/kg/capability/release_governance
https://knowledge-atlas.local/kg/decision/launchpad_model
https://knowledge-atlas.local/kg/platform/salesforce
```

Ids are lowercase, snake_case, and deterministic — the same object always yields
the same URI, and collision-suffixed ids stay unique. No random ids are minted.

### Exported files

`catalog rdf-export` writes four files under `exports/rdf/` (Turtle by default;
`--format json-ld` and `--format nt` are also supported):

- `ontology.ttl` — the ten object classes (`kg:Capability` … `kg:Process`), the
  relationship predicates (`kg:supports`, `kg:dependsOn`, `kg:implements`,
  `kg:affects`, `kg:relatedTo`, `kg:ownedBy`, `kg:mentions`, `kg:references`),
  and the provenance vocabulary (`kg:Evidence`, `kg:supportedBy`, …).
- `knowledge.ttl` — one resource per approved object, with `rdf:type`,
  `rdfs:label`, and `kg:confidence`.
- `relationships.ttl` — approved object-to-object triples.
- `provenance.ttl` — named `kg:Evidence` resources (`kg:sourceArtifact`,
  `kg:quote`, `kg:confidence`) linked from objects via `kg:supportedBy`.

Keeping the four graphs in separate files keeps the export forward-compatible
with named graphs in a quad store.

### Commands

- `catalog rdf-export [--out DIR] [--format turtle|json-ld|nt]` — write the four
  RDF files and print export counts.
- `catalog rdf-validate [--out DIR]` — re-parse each exported file with rdflib.
- `catalog rdf-stats` — show objects / relationships / evidence that would be
  exported (APPROVED only).
- `catalog fuseki-load [--out DIR]` — validate, then upload ontology → knowledge
  → relationships → provenance via the SPARQL Update endpoint.
- `catalog fuseki-clear` — `CLEAR ALL` triples from the dataset.

Fuseki connection details live in `config/jena.yml` (`fuseki.endpoint`,
`fuseki.dataset`); a missing file falls back to `http://localhost:3030/knowledge-atlas`.


### Local Fuseki with Docker

A `docker-compose.yml` is included for local Apache Jena Fuseki development. It
starts an update-enabled in-memory dataset at `http://localhost:3030/knowledge-atlas`,
matching the default `config/jena.yml` endpoint.

```bash
docker compose up -d fuseki
catalog rdf-export
catalog fuseki-load
```

### Example queries

`queries/` holds ready-to-run SPARQL (`.rq`) examples — `all_capabilities.rq`,
`all_decisions.rq`, `all_relationships.rq`, `related_capabilities.rq`. After
`catalog fuseki-load`, the headline query returns the approved capabilities:

```sparql
PREFIX kg: <https://knowledge-atlas.local/kg/>
SELECT ?capability WHERE { ?capability a kg:Capability . }
```

## Knowledge Explorer and SPARQL query layer

The `catalog.graph` package is a **query and exploration layer over the approved
knowledge graph**. Its job is to let you discover, navigate, inspect, and
*validate* knowledge using only SPARQL, graph algorithms, and graph metrics —
explicitly **before** (and without) any GraphRAG, LLM, vector search, or
embeddings. The graph must prove its usefulness on its own first.

```
SQLite (system of record) → RDF projection → GraphClient (SPARQL)
                                           → NetworkX (algorithms)
```

By default every `catalog graph` command rebuilds the approved RDF projection
in memory straight from SQLite and runs **real SPARQL** against it with rdflib —
so nothing here needs a running server. Add `--fuseki` to route the same SPARQL
to a live Apache Jena Fuseki endpoint (`config/jena.yml`) instead.

### The query layer

`GraphClient` (`catalog.graph.client`) executes SPARQL and manages the on-disk
query library: `execute_query()`, `load_query()`, `list_queries()`,
`save_query()`. The library lives in `queries/` as `.rq` files —
`all_capabilities`, `all_decisions`, `all_teams`, `all_platforms`,
`related_objects`, `top_relationships`, `object_dependencies`,
`knowledge_domains`, `evidence_for_object`, and more.

- `catalog graph query` — list the saved queries.
- `catalog graph query <name>` — run one, e.g. `catalog graph query all_capabilities`.

### Exploration commands

- `catalog graph search "release"` — search `rdfs:label` / description, returning
  matching objects, their types, and relationship counts.
- `catalog graph show <id>` — object detail: name, type, description, confidence,
  connected objects (grouped by relationship), evidence count, relationship count.
- `catalog graph neighbors <id>` — connected objects grouped by relationship type.
- `catalog graph path <id1> <id2>` — shortest path between two objects.
- `catalog graph impact <id>` — what may be affected by a change, grouped by type
  (connected capabilities, decisions, teams, platforms, …).

### Validation and analysis

- `catalog graph health` — knowledge validation: objects without relationships or
  evidence, relationships without evidence, low-confidence objects, duplicate
  candidates, disconnected subgraphs, and the most connected nodes.
- `catalog graph domains` — knowledge domains (by object type) with object counts,
  relationship counts, and the most central concept in each.
- `catalog graph metrics` — NetworkX analysis (degree & betweenness centrality,
  connected components, density, clusters); prints the most central objects and
  writes the visualization bundle `exports/graph/{nodes,edges,metrics}.json`.

### Graph export

- `catalog graph export-gexf` — GEXF for **Gephi**.
- `catalog graph export-graphml` — GraphML for **Neo4j** / yEd / Cytoscape.
- `catalog graph export-json` — node-link JSON for **NetworkX** / D3.

Files are written under `exports/graph/` (override with `--out DIR`).

### Interactive explorer

`catalog graph explore` opens a small Rich-powered, read-only terminal REPL —
`search`, `show`, `neighbors`, `evidence` — over the same SPARQL layer.

With this layer you can answer, **using only the graph**: what capabilities are
connected to Release Governance, what decisions affect Salesforce, what the most
central concepts are, what evidence supports a capability, and the shortest path
between a team and a capability — proving the graph's usefulness before any AI
layer is added.

## GraphRAG knowledge assistant

The `catalog.graphrag` package is a conversational analyst that reasons over the
**approved** knowledge graph, its approved relationships, evidence, and source
documents. The graph drives *retrieval*; the LLM performs *reasoning*. It is
deliberately **not** naive RAG: there is no document search, no full-text scan,
no vector database, and no embedding retrieval. Graph retrieval is mandatory, and
nothing unapproved can ever reach the model.

```
Question
    -> Intent analysis       (search object, type, relationship, reasoning type)
    -> Graph retrieval        (match objects, expand neighbourhood via SPARQL)
    -> Evidence retrieval     (approved relationships + supporting quotes)
    -> Context builder        (compact, deterministic, traceable context)
    -> LLM                    (reasoning only, over the supplied context)
    -> Traceable answer       (objects + relationships + evidence + confidence)
```

### Graph-first retrieval

Retrieval never touches documents first. A question's named objects are resolved
to stable ids, the graph neighbourhood is expanded to a configurable depth
(1, 2, or 3; **default 2**) over the SPARQL/NetworkX projection, the approved
relationships inside that neighbourhood are gathered, and finally supporting
evidence (document id, quote, confidence) is pulled per object — exactly the walk
`Release Governance → Launchpad Model → Release Management → Test & Release Team`.

### Intent analysis

Each question is parsed *deterministically* (no LLM) into a search object, object
type, relationship focus, and a **reasoning type** — `lookup`, `path`, `impact`,
`evidence`, `domain`, or `comparison` — which shapes both retrieval and the
prompt. Keeping intent rule-based makes the only non-deterministic step the final
reasoning over an already-fixed context.

### Hallucination controls

The assistant behaves like a knowledgeable analyst, not a guessing chatbot:

- It answers **only** from the retrieved graph context; the system prompt forbids
  inventing objects, relationships, documents, or quotes.
- Every answer carries its citations — knowledge objects, evidence handles
  (`[E1]`), and documents — and a **confidence** band.
- If no object matches or no evidence is retrieved, it declines *before* calling
  the model and replies exactly: **"No supporting evidence found."**

### Answer confidence

Confidence is computed from the retrieval (not the model's self-assessment),
blending object confidence, relationship confidence, evidence confidence, and
coverage into a **High / Medium / Low** band.

### Conversation memory

A session remembers each turn's question and retrieved objects, so follow-ups
resolve referents:

```
Q1: "What supports Release Governance?"
Q2: "What risks are associated with that?"     # "that" -> Release Governance
```

### Commands

```bash
catalog ask "What supports Release Governance?"
catalog ask "What capabilities depend on Salesforce?" --depth 3
catalog ask "What decisions affect Test & Release?" --model qwen3:14b
catalog ask "What risks affect Salesforce?" --show-context --show-sparql --show-evidence

catalog explain "Release Governance"          # description, connections, evidence
catalog compare "Release Governance" "Platform Governance"
catalog impact "Salesforce"                    # capabilities/decisions/risks/teams affected
catalog path-reason "Release Governance" "Salesforce"   # retrieve path, LLM explains
```

The active LLM provider is selected with `--llm-config` (default `config/llm.yml`)
and reuses the same `OllamaProvider` / `OpenAIProvider` abstraction as the
semantic layer, so adding a provider needs no changes here. By default the
assistant runs SPARQL against the in-memory projection built from SQLite (no
Fuseki required); `--fuseki` reroutes the same SPARQL to a live endpoint.

### Observability

Every answered question logs (at `-v`) its reasoning type, the counts of objects,
relationships, and evidence retrieved, the prompt size, the response time, and
the resulting confidence band.

## Token cost and extraction quality

Every LLM call in the pipeline reports the tokens it used; the catalog now
captures that usage (instead of discarding it), prices it, and records one row
per call so the **cost of extraction** is measurable and can be weighed against
the quality of what came back.

Four call sites are tracked, each tagged with an `operation`:

- `classify` — semantic classification (one call per document chunk),
- `vision-extract` — vision transcription of hard-to-read PDF pages,
- `ask` — GraphRAG questions,
- `merge` — the consolidation merge judge.

Each call is captured as a row in the regenerable `llm_usage` table: the
operation, the artifact it served (for `classify`/`vision-extract`), the model
and provider, input/output tokens (plus Anthropic cache tokens), latency, and the
USD cost computed at record time.

### Pricing

Rates live in `config/pricing.yml`, in USD per 1,000,000 tokens:

```yaml
currency: USD
unit: per_1m_tokens
models:
  claude-sonnet-4-5: { input: 3.00, output: 15.00, cache_read: 0.30, cache_write: 3.75 }
  gpt-5.5:           { input: 5.00, output: 15.00 }
  qwen3:14b:         { input: 0.0,  output: 0.0 }   # local models are free
```

A model that is not listed is *unpriced*: its tokens are still recorded, but
`cost_usd` is left empty and the report flags it. Editing the rates affects only
calls recorded afterwards — past spend is reported as it was actually billed.

### Reporting

```bash
catalog cost-report                              # totals + breakdowns (table)
catalog cost-report --top 50                     # more per-document rows
catalog cost-report --format json --out exports/cost.json
```

The report shows total tokens and cost, a breakdown **by operation** and **by
model**, the **cost per document** (highest first), and a **cost vs. quality**
view that places each document's spend next to the model's own classification
confidence — so you can see whether the most expensive documents are also the
ones the model was most (or least) sure about.

Because every call is attributed to an operation, model, artifact, latency, and
cost, this ledger is also the basis for the optimization strategy below (spotting
over-triggered vision pages, runaway chunk counts, or a cheaper model that holds
quality). Local models priced at `0.0` make a fully offline run cost nothing.

## Adaptive LLM optimization

The pipeline keeps LLM cost and latency low while preserving classification
quality by **spending tokens in proportion to difficulty**. Three mechanisms
work together (full design in [`docs/llm-optimization.md`](docs/llm-optimization.md)):

- **Prompt caching.** The large, constant classification instructions live in
  the system prompt and are cached (Anthropic `cache_control`), so repeated calls
  in a run are billed at the cheaper cache-read rate and skip re-processing ~1k
  tokens. Same prompt in, same output out — a pure win. Toggle with
  `prompt_cache` in `config/llm.yml`.
- **Adaptive model routing.** A cheap, deterministic complexity read (length,
  symbol density, equation markers, normative/standards language — no LLM) routes
  ordinary documents to a fast model (`claude-haiku-4-5`) and complex ones —
  standards, regulations, engineering codes with equations, long dense files — to
  a deep model (`claude-sonnet-4-5`).
- **Confidence-based escalation.** When the fast model is unsure (low
  `type_confidence`), that one document is re-run on the deep model and the deep
  result is kept — so the strong model is spent only where it is needed.

Routing ships **enabled** in `config/llm.yml`; set `routing.enabled: false` to
classify every document with the single configured `model`. The savings are
visible in `catalog cost-report` (Haiku-rate tokens, cache-read tokens, and the
by-model breakdown).

```yaml
routing:
  enabled: true
  fast_model: claude-haiku-4-5
  deep_model: claude-sonnet-4-5
  complexity_threshold: 0.5
  escalate_below_confidence: 0.6
  fast_max_chunks: 6
```

## Knowledge governance and continuous operations

The `catalog.governance` package turns the consolidated graph from a periodically
rebuilt artifact into a **continuously governed knowledge platform**. Its single
goal is trust: every object, relationship, and piece of evidence carries an
origin, an owner, a review state, and a freshness state, so the graph stays
current, traceable, reviewable, explainable, and maintainable.

It adds no retrieval, GraphRAG, vector, or agent features — it is pure governance
over the SQLite system of record.

### What governance tracks

Six tables hold curated governance state. Crucially they reference object ids
*softly* (by value, not by an enforced foreign key), so they **survive a
`consolidate`** — which deletes and recreates `knowledge_objects` — and the
ownership, review decisions, and freshness history are never lost.

| Table | Purpose |
| --- | --- |
| `knowledge_owners` | who owns each object (Team / Person / Domain) |
| `knowledge_lifecycle` | freshness + review-workflow state, `last_seen_at`, history |
| `knowledge_quality` | the latest 0–100 quality score and its factors |
| `knowledge_alerts` | generated operator alerts |
| `knowledge_change_log` | the append-only audit trail |
| `knowledge_reviews` | the human review-action audit trail (reused) |

### The freshness lifecycle

Freshness answers "is this still current?" from how long it has been since fresh
evidence was last seen for an object. The rules are configurable
(`config/governance.yml`):

```
seen recently            -> FRESH
no evidence for 180 days -> AGING
no evidence for 365 days -> STALE
archived by a reviewer   -> ARCHIVED
```

A continuous `freshness_score` decays linearly from 1.0 (seen today) to 0.0 at
the archive horizon, feeding the quality score.

### Quality scoring

Every object gets a 0–100 quality score blending six factors: evidence count,
review status, freshness, relationship consistency, whether an owner is assigned,
and confidence. It is the single number that answers "how much should I trust
this?" — the worked example being a well-owned, approved *Release Governance*
(92) outranking a pending, unowned *Launchpad Model* (71).

### Change detection, drift, and evolution

Each scan diffs the current graph against the previous one and appends to the
audit trail: new/removed objects, new/removed relationships, confidence changes,
ownership changes, and freshness transitions. **Drift detection** flags
disappearing evidence, established objects vanishing, and terminology changes
(e.g. *Launchpad Model* in 30 documents being replaced by *Mission Delivery
Model*). `catalog governance history <object>` then answers, for any object, why
it exists, what evidence supports it, who approved it, when it was reviewed, and
what changed.

### Review workflow, alerts, and orphans

Objects move through a review workflow — `PENDING_REVIEW`, `NEEDS_ATTENTION`,
`APPROVED`, `ARCHIVED`, `REJECTED`. Approving an object also pins its
consolidation status so it flows into the RDF projection; rejecting/archiving
removes it. Each scan regenerates alerts for stale knowledge, stale reviews,
orphaned objects, missing owners, conflicting evidence, duplicate objects and
relationships, quality degradation, and drift. Orphan detection finds objects
without evidence/relationships/owners, relationships without evidence, and
evidence without an object.

### Domain governance

Objects are mapped to business domains through their documents' classifications.
Domains are not predefined: they are discovered from the data, like every other
object in the catalog, so a domain exists once a document has been classified
under it. Each domain reports an object count, average quality, average
freshness, and review backlog.

### Automated ingestion

`catalog governance ingest` runs the whole pipeline on a cadence
(`daily` / `weekly` / `manual`):

```
scan -> extract -> discover-links -> consolidate -> rdf-export -> governance scan
```

A last-run marker drives the schedule; `--force` runs regardless, and a failing
step is recorded without aborting the cadence.

### Commands

```bash
catalog governance scan                 # refresh lifecycle, detect change/drift, score, alert
catalog governance dashboard            # knowledge health at a glance
catalog governance review-queue         # objects awaiting review
catalog governance stale                # stale / aging knowledge
catalog governance quality              # quality scores (--ascending for worst first)
catalog governance orphaned             # orphan detection
catalog governance alerts               # open alerts (--type to filter)
catalog governance drift                # detected knowledge drift
catalog governance changes              # recent audit-trail entries

catalog governance history <object>     # full provenance + change history of one object
catalog governance approve <object>     # trusted, exported to RDF
catalog governance archive <object>     # retired, kept for history
catalog governance reject <object>      # not trusted, excluded
catalog governance flag <object>        # mark as needing attention
catalog governance assign-owner <object> <Team|Person|Domain> "<owner>"
catalog governance owners               # ownership assignments
catalog governance domains              # per-domain governance health

catalog governance export               # quality_report.json, governance_report.json,
                                        #   knowledge_health.json, change_log.json
catalog governance ingest [--schedule daily|weekly|manual] [--force]
```

Rules are configured in `config/governance.yml` (freshness thresholds, quality
weights, drift sensitivity, and the ingestion cadence); every value falls back to
a sensible default, so governance runs out of the box. Domains themselves are not
configured — they are discovered from the documents' semantic classifications.

## Compliance and standards

The compliance layer turns the platform into an auditable compliance posture. It
handles two new classes of document — **standards/regulations** (the law itself:
GDPR, ISO 27001, NIS2, internal policy) and the **compliance-proof** documents
that show the organization meets them — and answers *"are we compliant with X,
and prove it?"* and *"where are our gaps?"*.

It is a natural extension of the existing invariants, not a new system: nothing
is a fact without traceable evidence, nothing is trusted until a human approves
it, and the assistant declines rather than hallucinates. Those rules *are* an
audit posture.

### The model

Two new knowledge-object types join the existing ten, so they flow through
consolidation, governance, RDF, and GraphRAG like everything else:

- **`Standard`** — a standard/regulation/policy family (e.g. *ISO 27001:2022*).
- **`Requirement`** — one normative clause/article/control (e.g. *GDPR Art. 32*).

Organizational **controls are not a new type** — they are the existing
`Capability` / `Process` / `Platform` / `Technology` objects already consolidated
from internal documents. Compliance is the *mapping* between them, expressed with
three new relationship predicates:

- `mandated_by` — `Requirement → Standard` (which standard a requirement belongs to)
- `satisfies` — `control → Requirement` (a control claims to meet a requirement)
- `supersedes` — versioning of an amended standard or requirement

### Two ways in

Requirements arrive by **either** path, converging on the same
`candidate_requirements` table and the same `Standard`/`Requirement` objects:

1. **LLM extraction** — a document classified as a `Standard`/`Regulation`/
   `Governance` has its clauses mined into candidate requirements during
   `catalog classify`.
2. **Curated import** — a maintained YAML/CSV catalog of a well-known framework:

   ```bash
   catalog compliance import config/standards/iso27001.yml
   ```

### Assess, prove, and find gaps

`catalog compliance assess` evaluates every requirement against the controls
that `satisfy` it, gathers their evidence, and **derives** a status — `SATISFIED`,
`PARTIAL`, `GAP`, or `NOT_APPLICABLE` — into a dedicated *assessment record*
(status + assessor + the standard version it was assessed against + linked
evidence). As everywhere on the platform, the engine only *proposes*: an
assessment is written `PROPOSED` and a requirement counts toward coverage only
once a human **approves** it. A re-run preserves prior review decisions, exactly
like `consolidate`. The evidence invariant carries over too — a `SATISFIED` /
`PARTIAL` claim must be backed by at least one evidence quote, or it is recorded
as a `GAP` instead.

```bash
catalog compliance import config/standards/iso27001.yml
catalog consolidate
catalog compliance assess
catalog compliance coverage          # per-standard coverage (approved claims only)
catalog compliance gaps              # requirements with no approved satisfying control
catalog compliance prove "GDPR Art. 32"   # cited proof, or "No supporting evidence found."
catalog compliance approve <assessment_id>
```

`catalog ask "GDPR Art. 32" --prove` answers the same proof question through the
GraphRAG entry point (graph-first, no LLM), and the REST API exposes the whole
surface under `/api/compliance/*` (standards, requirements, coverage, gaps,
assessments, `prove`, approve/reject, and an `assess` job). The SPARQL queries in
`queries/compliance_*.rq` cover gaps, coverage, and the control→requirement map.

Rules are configured in `config/compliance.yml` (which object types act as
controls, the coverage threshold, and the evidence-staleness horizon that
downgrades stale `SATISFIED` claims to `PARTIAL`); every value has a sensible
default, so compliance runs out of the box. See
[`docs/compliance.md`](docs/compliance.md) for the full design.

## REST API

The catalog is also exposed through a thin, local-first REST API (FastAPI +
Pydantic over the existing services) so that `navigate-compass` or any other
client can consume the knowledge platform. The API **does not replace the CLI**:
both call the same service/repository layer, and the route handlers contain no
business logic and no SQL.

See [`docs/navigate-api.md`](docs/navigate-api.md) for the client-facing
contract notes — including the domains resource, the knowledge-growth trend, the
change-log feed, and the per-row child counts that clients such as
`navigate-compass` consume.

Run the server locally:

```bash
catalog api                              # or: navigate api
catalog api --host 127.0.0.1 --port 8000 # explicit bind
catalog api --no-reload                  # disable auto-reload
```

Then open the interactive docs:

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- OpenAPI schema: <http://127.0.0.1:8000/openapi.json>

> The Python package is named `catalog`, so the server lives in
> `catalog.api` and the command is `catalog api`. `navigate api` is provided as
> an alias for the same entry point.

### Principles

- **local-first**: binds to `127.0.0.1` by default and is never exposed
  externally unless you change `host` yourself.
- **read-heavy with safe defaults**: no external calls happen unless explicitly
  enabled (`enable_graphrag`, `enable_classify`).
- **consistent contract**: every list endpoint paginates
  (`{items, limit, offset, total}`) and every error has the same shape
  (`{error, message, details}`).

### Endpoints

| Group | Endpoints |
|-------|-----------|
| Base | `GET /api/health`, `GET /api/stats` |
| Artifacts | `GET /api/artifacts`, `GET /api/artifacts/{id}`, `.../links`, `.../evidence`, `POST .../rescan`, `.../extract`, `.../classify` |
| Links | `GET /api/links`, `GET /api/links/stats`, `GET /api/links/top-targets` |
| Knowledge | `GET /api/knowledge-objects`, `GET /api/knowledge-objects/{id}`, `.../relationships`, `.../evidence`, `.../mentions`, `POST .../approve`, `.../reject`, `.../archive` |
| Relationships | `GET /api/relationships`, `GET /api/relationships/{id}`, `POST .../approve`, `.../reject` |
| Evidence | `GET /api/evidence`, `GET /api/evidence/{id}` |
| Governance | `GET /api/governance/{dashboard,review-queue,stale,orphaned,alerts,quality}`, `GET /api/governance/domains`, `.../domains/{name}`, `GET /api/governance/changes`, `GET /api/governance/growth` |
| Graph | `GET /api/graph/{nodes,edges,export-json}`, `GET /api/graph/object/{id}/{neighbors,impact}`, `GET /api/graph/path?source=&target=&max_depth=` |
| GraphRAG | `POST /api/ask` (501 until `enable_graphrag` is set) |
| Jobs | `POST /api/jobs/{scan,extract,discover-links,classify,consolidate}`, `GET /api/jobs`, `GET /api/jobs/{id}` |

### Configuration

`config/api.yml` (all keys optional; safe defaults are used when the file is
absent):

```yaml
host: "127.0.0.1"
port: 8000
reload: true
cors_origins:
  - "http://localhost:5173"
require_api_key: false
api_key_env: NAVIGATE_API_KEY
enable_graphrag: false
enable_classify: false
```

Set `require_api_key: true` and put the key in your shell environment or local
`.env` (not in `config/api.yml`) to require `Authorization: Bearer <token>` on
every `/api` request:

```bash
cp .env.example .env
# edit .env so NAVIGATE_API_KEY has a long random value
```

Long-running pipeline operations are exposed as **jobs**, tracked in SQLite
(`id`, `job_type`, `status`, `started_at`, `completed_at`, `error_message`,
`result_summary`) so a client can trigger them and poll for completion.

### Running in Docker

A `Dockerfile` and a `api` service in `docker-compose.yml` package the REST API:

```bash
docker compose up --build api      # build + run the API
# API is now on http://127.0.0.1:8000/docs
```

The SQLite index and document cache are shared with the host via the local
`data/` and `cache/` directories, so Docker and local CLI commands see the same
data. `config/` and `queries/` are mounted from the host so you can edit them
without rebuilding. The optional Fuseki triplestore is a separate service
(`docker compose up fuseki`).

**File permissions (write access to the SQLite DB).** The API does not only read
the database — review actions such as approving knowledge objects and
relationships **write** to `data/catalog.sqlite`, and SQLite needs write access
to both the file and the `data/` directory (for its journal/WAL and lock files).
Because `data/` is a host bind mount, the container process must own (or be able
to write) those host files. If it cannot, reads still succeed but every write
fails with `sqlite3.OperationalError: attempt to write a readonly database` — a
`500` from the API, which a reverse proxy in front of it reports as a **502 Bad
Gateway**. The same error hits the host CLI (`catalog scan`, `catalog init-db`)
when `data/` is owned by the container user. Navigate now checks this before
opening the database and prints an actionable message instead of a raw SQLite
traceback, pointing you at the fix below.

**Recommended: the shared-group setup script.** Run it once. It creates `data/`
and `cache/`, gives them a shared group, makes them group-writable with the
setgid bit (so files created by *either* the host CLI or the container stay
writable by both), fixes any existing SQLite files, and records
`NAVIGATE_UID`/`NAVIGATE_GID` in `.env` (which Compose reads automatically):

```bash
./scripts/dev-permissions.sh          # uses your uid / primary gid by default
docker compose up --build api          # container runs as the same uid:gid
```

Override the shared group to use a *dedicated* group shared by host and
container rather than your primary one:

```bash
NAVIGATE_GID=$(getent group developers | cut -d: -f3) ./scripts/dev-permissions.sh
```

Use `NAVIGATE_UID`/`NAVIGATE_GID`, **not** `UID`/`GID`: bash treats `UID`/`GID`
as read-only, so `export UID=$(id -u)` silently does nothing. The script writes
`.env` for you; to set them by hand for a one-off run instead:

```bash
NAVIGATE_UID=$(id -u) NAVIGATE_GID=$(id -g) docker compose up -d api
```

Alternatively, hand the data to the image's built-in user (`navigate`, uid
10001 — the default when `NAVIGATE_UID`/`GID` are unset) with
`sudo chown -R 10001:10001 ./data ./cache`; after that, host CLI writes to the
DB may need `sudo`, which is why the shared-group approach is preferred.

**Verify** write access from the host (both commands must succeed):

```bash
touch data/testfile && rm data/testfile
sqlite3 data/catalog.sqlite "CREATE TABLE test_write(id INTEGER); DROP TABLE test_write;"
```

Inside the container the server binds to `0.0.0.0`, but compose publishes the
port only to the host loopback (`127.0.0.1:8000:8000`), so the API stays
local-first and is **not** exposed externally by default — change the published
address yourself (and enable `require_api_key`) if you intend to share it.

LLM credentials are not baked into the image. The `api` service forwards
`ANTHROPIC_API_KEY` and `OPENAI_API_KEY` from the host environment, which Docker
Compose also reads from a `.env` file in the project directory. Only the
provider selected in `config/llm.yml` needs a key. So to enable Claude
classification or GraphRAG (the default provider) in the container, set its key
in `.env`:

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env   # or OPENAI_API_KEY=sk-... for provider: openai
docker compose up --build api
```

Keys are never written to the image or committed (`.env` is git-ignored). For a
one-off run you can pass one directly:
`ANTHROPIC_API_KEY=sk-ant-... docker compose run -e ANTHROPIC_API_KEY api`.

## Future extension points

The consolidated knowledge objects are designed to support later phases without
changing the scanner, extraction, or the semantic layer. These future modules
are **not** implemented yet:

- a **visualization UI** consuming the `exports/graph/{nodes,edges,metrics}.json`
  bundle (and the GEXF / GraphML exports) the explorer now produces.

The RDF projection and Jena/Fuseki loader are implemented in the `catalog.rdf`
package; the SPARQL query and exploration layer in the `catalog.graph` package.

Earlier deterministic phases also remain open for extension: link resolution,
broken-link checking, and fetching metadata from SharePoint/Confluence/ADO.

## Extending the scanner

Future processing subscribes to scan events instead of modifying the scanner:

```python
from catalog.scanner import Scanner
from catalog.events import ScanStatus

scanner = Scanner(db_path="data/catalog.sqlite")

def on_new_or_changed(event):
    print(event.status, event.artifact.path, event.artifact.id)

scanner.event_bus.subscribe(on_new_or_changed, statuses={ScanStatus.RAW, ScanStatus.CHANGED})
scanner.scan("config/sources.yml")
```

A failing subscriber is logged and isolated so it can never corrupt indexing.

## Development

Run tests:

```bash
pytest
```

Run the CLI without installing the package:

```bash
PYTHONPATH=src python -m catalog.cli stats
```
