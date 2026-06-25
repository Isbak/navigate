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

## 6. (Optional) Sync cloud connectors

Pull content from Google Drive, GitHub repos, SharePoint/OneDrive, Confluence,
or Jira/Azure DevOps directly into the catalog — no manual downloads needed.
Downloaded files land in `connector_cache/` and flow through the same
extract → classify → consolidate pipeline as local files.

**Quick start:**

1. Edit `config/connectors.yml` (a commented template is provided) and uncomment
   the connector(s) you want. Set credentials via environment variables:

   ```bash
   export GITHUB_TOKEN=ghp_…
   ```

2. Sync:

   ```bash
   catalog connector list              # verify what's configured
   catalog connector sync              # sync all enabled connectors
   catalog connector sync eng-repos    # sync one connector by name
   catalog connector sync --dry-run    # preview without downloading
   catalog connector status            # per-connector file counts
   ```

3. Process new content normally:

   ```bash
   catalog extract && catalog classify
   ```

**Supported sources:**

| Type | `type:` value | Auth |
|---|---|---|
| GitHub repos | `github` | PAT (`GITHUB_TOKEN`) |
| Google Drive | `google_drive` | Service-account JSON key |
| SharePoint / OneDrive | `sharepoint` | Entra ID client credentials |
| Confluence | `confluence` | Atlassian API token |
| Jira | `jira` | Atlassian API token |
| Azure DevOps | `azure_devops` | PAT |

> **Note:** `connector_cache/` is gitignored. The `connector_file_map` table in
> the database tracks which remote items have been downloaded, enabling
> incremental re-syncs that only fetch new or changed content.

## 7. (Optional) Choose an extraction mode

By default, `catalog extract` uses `mode: fast` — text-only extraction, no API
calls or extra installs. Two additional modes are available for higher quality:

### mode: enhanced (MarkItDown, office formats)

Converts DOCX, PPTX, and XLSX to Markdown using
[MarkItDown](https://github.com/microsoft/markitdown) (Microsoft). Tables
become proper `| col |` Markdown rows visible to the LLM classifier. PDFs
still use PyMuPDF (unchanged). No ML models; negligible overhead.

```bash
pip install "knowledge-catalog[markitdown]"
```

```yaml
# config/extraction.yml
mode: enhanced
```

> **Note:** Install only the base `markitdown` package. Avoid `markitdown[all]`
> in environments that use a system `cryptography` package — the `[all]` extras
> pull in pdfminer which can cause a Rust/cffi conflict.

### mode: docling (IBM Docling, all formats)

Uses [IBM Docling](https://github.com/DS4SD/docling) for superior PDF reading
order, table extraction (including multi-column PDFs), and built-in OCR for
scanned pages. Also populates `knowledge_evidence.page_number` for every quote
in the knowledge graph. Requires ~1.5 GB model download on first use.

```bash
pip install "knowledge-catalog[docling]"
```

```yaml
# config/extraction.yml
mode: docling
```

> **Trade-off:** Docling loads a DocLayNet layout model (5–15 s cold start,
> ~400 MB RAM). Extraction is serialised (not parallelised) to avoid concurrent
> model access. Use `mode: fast` for large batch re-extractions.

See the full [Docling vs MarkItDown study](../docling-markitdown-study.md) for
benchmark data, dependency analysis, and architectural rationale.

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

When a file disappears from disk and `scan` marks it `DELETED`, its raw
`candidate_*` rows are kept (so the document returns to the knowledge graph
if the file comes back). The next `catalog consolidate` run — in any mode,
including `--all-sources` — automatically excludes `DELETED` artifacts and
drops any knowledge objects or relationships that were derived solely from
them. Use `catalog clean-source <path>` to also remove the candidate rows
permanently.
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
