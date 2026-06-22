# Catalog your files

**Goal:** index selected local folders in place so Navigate knows every
document, what changed since the last scan, and which files are duplicates — all
without ever moving or modifying a source file.

## Prerequisites

- Python 3.11+ and the package installed (`pip install -e '.[dev]'`). See the
  [README install section](../../README.md#quickstart).
- A folder or two you want to index.

## 1. Choose what to index

Edit `config/sources.yml` to list local folders. Paths support `~`; missing
directories are skipped with a warning.

```yaml
sources:
  - path: "~/Documents"
    source_system: "local_laptop"
  - path: "~/OneDrive"
    source_system: "onedrive_sync"

exclude:
  - "**/.git/**"
  - "**/node_modules/**"
  - "**/~$*"
  - "**/.DS_Store"
```

Navigate indexes `.docx`, `.pptx`, `.xlsx`, `.pdf`, `.md`, and `.txt` — plus
source code when **code-aware indexing** is on (the default). To index documents
only, set `index_code: false` in `config/sources.yml`. (To index a code
repository, see [Ground an AI agent in your code](ground-an-ai-agent-in-your-code.md).)

## 2. Initialize the database

```bash
catalog init-db
```

This creates the local SQLite index under `data/` (by default
`data/catalog.sqlite`). Source documents are never touched — only extracted text
and metadata are cached under `cache/`.

## 3. Scan

```bash
catalog scan
```

The scan walks every configured folder, hashes each file, classifies it against
the existing index, and prints a summary it also stores for later:

```
Files scanned: 823
New files: 18
Modified files: 7
Deleted files: 2
Duplicates: 31
```

## 4. Inspect the catalog

```bash
catalog stats             # latest scan run + catalog totals
catalog show-duplicates   # files sharing an identical SHA-256 content hash
```

## 5. (Optional) Watch for changes

Keep the catalog current by watching the configured folders and rescanning
changed supported files after a debounce delay:

```bash
catalog watch
```

## Next step

Your files are indexed. Continue with
[Discover and classify links](discover-and-classify-links.md), then
[Build a knowledge graph](build-a-knowledge-graph.md).

---

## How it works (data model)

Every artifact carries a `scan_status` describing what the most recent scan
found:

- `RAW` — newly discovered file, never seen before
- `CHANGED` — known path whose content hash changed
- `UNCHANGED` — known path with identical content
- `DELETED` — previously indexed path no longer present on disk
- `DUPLICATE` — content hash already seen at another path during the scan

The `artifacts` table holds stable document metadata keyed by source `path`:

- `path` — absolute source path (primary key; never modified)
- `id` — content-addressed identity `doc_<first_12_sha256_chars>`. Byte-identical
  files share the same `id`, which is how duplicates are detected.
- `filename`, `file_type`, `size_bytes`
- filesystem timestamps (`created_at`, `modified_at`), `sha256`, `source_system`
- `scan_status`, `first_seen_at`, `last_scanned_at`

`scan_runs` stores per-scan statistics (files scanned, new, changed, unchanged,
duplicate, deleted, plus start/finish timestamps); `catalog stats` reports the
latest run.

Internally the scanner is a small pipeline — `scanner → artifact queue →
database`, with a scan event bus that publishes one event per processed artifact
so extractors can subscribe without modifying the scanner. All `catalog`
commands accept alternate paths, e.g.:

```bash
catalog --config config/sources.yml --db data/catalog.sqlite --cache cache scan
```
