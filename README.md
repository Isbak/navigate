# Knowledge Catalog

A local-first professional knowledge catalog that indexes selected folders in place. It stores document metadata in SQLite, detects what changed between scans, extracts text into a local cache, discovers hyperlinks, and can optionally watch files for changes.

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
  `extraction.py` subscribes to `RAW`/`CHANGED` events to cache text and links.

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

Show discovered links:

```bash
catalog show-links
```

All commands accept alternate paths:

```bash
catalog --config config/sources.yml --db data/catalog.sqlite --cache cache scan
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

`links` contains hyperlinks found in extracted text:

- source `path`
- target URL
- optional anchor text
- classified target system and target type
- discovery timestamp

## Link classification

URL patterns are classified into:

- `sharepoint`
- `onedrive`
- `confluence/wiki`
- `jira`
- `azure_devops`
- `github`
- `teams`
- `external`
- `unknown`

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
