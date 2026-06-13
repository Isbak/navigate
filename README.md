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

## Future extension points

The normalized links and classifications produced here are designed to support
later phases without changing extraction or the scanner: link resolution,
broken-link checking, fetching metadata from SharePoint/Confluence/ADO, LLM
relationship classification, RDF export, and graph loading into Jena/Fuseki.

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
