# Knowledge Catalog

A local-first professional knowledge catalog that indexes selected folders in place. It stores document metadata in SQLite, extracts text into a local cache, discovers hyperlinks, and can optionally watch files for changes.

## Design principles

- Source documents are never moved, renamed, or modified.
- The catalog is an index, not a document store.
- Only extracted text and metadata are cached under `cache/`.
- The SQLite database remains local under `data/` by default.
- The module layout is intentionally simple so future modules such as `llm_enrichment.py`, `rdf_export.py`, and `fuseki_loader.py` can be added without changing scanner, database, or CLI boundaries.

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

## Data model

`artifacts` contains stable document metadata:

- `id` generated as `doc_<first_12_sha256_chars>`
- absolute source `path`
- `filename`, `file_type`, `size_bytes`
- filesystem timestamps
- `sha256`
- `source_system`
- `scan_status`
- `last_scanned_at`

`links` contains hyperlinks found in extracted text:

- source artifact ID
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

## Development

Run tests:

```bash
pytest
```

Run the CLI without installing the package:

```bash
PYTHONPATH=src python -m catalog.cli stats
```
