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
catalog extract
```

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
Two providers ship today and new ones are a one-line registration plus a small
subclass:

- `OllamaProvider` — talks to a local Ollama server (fully offline).
- `OpenAIProvider` — talks to the OpenAI Chat Completions API.

Both use only the standard library (no vendor SDK dependency). Configure the
active provider in `config/llm.yml`:

```yaml
provider: ollama

ollama:
  model: qwen3:14b
  host: http://localhost:11434

openai:
  model: gpt-5.5            # OPENAI_API_KEY is read from the environment

max_input_chars: 12000      # extracted text is truncated to this before prompting
```

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
merged; they surface as *duplicate candidates* for a human to review.

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

catalog knowledge-stats            # top capabilities/concepts/technologies, most
                                   # connected/mentioned, conflicts, duplicates
catalog show-object capability_release_governance
catalog search-knowledge "release"
catalog review-candidates          # PROPOSED objects/relationships + duplicates
catalog approve-object <id>
catalog reject-object <id>
catalog export-graph-json          # writes exports/graph/{nodes,edges}.json
```

After `catalog consolidate`, the success-criteria questions are answerable from
consolidated objects rather than individual documents: *what are the core
capabilities, which decisions are repeatedly referenced, and which concepts
connect multiple domains.*

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

### Example queries

`queries/` holds ready-to-run SPARQL (`.rq`) examples — `all_capabilities.rq`,
`all_decisions.rq`, `all_relationships.rq`, `related_capabilities.rq`. After
`catalog fuseki-load`, the headline query returns the approved capabilities:

```sparql
PREFIX kg: <https://knowledge-atlas.local/kg/>
SELECT ?capability WHERE { ?capability a kg:Capability . }
```

## Future extension points

The consolidated knowledge objects are designed to support later phases without
changing the scanner, extraction, or the semantic layer. These future modules
are **not** implemented yet:

- `graphrag_builder.py` — build a GraphRAG index.
- a **visualization UI** consuming the `nodes.json` / `edges.json` graph export.

The RDF projection and Jena/Fuseki loader described above are implemented in the
`catalog.rdf` package.

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
