# Discover and classify links

**Goal:** find every hyperlink inside your indexed documents, normalize it, and
classify where it points (SharePoint, Confluence, Azure DevOps, external web, …)
— deterministically, with no LLM.

## Prerequisites

- You have scanned your files — see [Catalog your files](catalog-your-files.md).

## 1. Build the extraction cache

Link discovery reads from the extraction cache, so extract first. This caches,
per artifact, `extracted.txt`, `links.json` (raw `{raw_url, anchor_text}`
pairs), and `metadata.json`:

```bash
catalog extract                                  # fast text extraction (default)
catalog extract --mode high-quality              # vision pass for hard PDF pages
catalog extract --path-glob '**/*standard*.pdf'  # only matching files
catalog extract --artifact-id doc_abc123         # only these ids (repeatable)
```

Extraction has two modes (configured in `config/extraction.yml`, overridable
with `--mode`):

- **fast** — text-only, offline, no API calls. Good default.
- **high-quality** — for PDFs, the fast pass runs first and only pages it cannot
  read well (scanned pages, figures, equations rendered as images) are rendered
  to an image and transcribed by the Claude provider in `config/llm.yml`. Needs
  network access and `ANTHROPIC_API_KEY`; cost stays proportional to the suspect
  pages.

## 2. Discover links

```bash
catalog discover-links                           # all cached artifacts
catalog discover-links --artifact-id doc_abc123  # a single artifact
```

A run reports a summary it stores in `link_scan_runs`:

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

## 3. Inspect and export links

```bash
catalog link-stats                               # totals, breakdowns, top references
catalog show-links --artifact-id doc_abc123      # links from one artifact
catalog show-links --system sharepoint           # links to one target system
catalog show-stale-links                          # links missing from the latest extraction
catalog export-links-csv                          # write exports/links.csv
```

## 4. (Optional) Tell Navigate which domains are internal

Create `config/link_patterns.yml` to declare which domains count as internal and
to extend system matching. When the file is absent, the built-in patterns are
used; `github.com` is treated as `external` unless a URL matches a configured
internal domain.

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

## Next step

With links discovered, continue to [Build a knowledge graph](build-a-knowledge-graph.md).

---

## How it works (data model)

Link handling is split into two stages that never mix concerns: **extraction**
writes raw `links.json` files (no database), and **link discovery** reads those
files, normalizes and classifies each URL with deterministic rules, and persists
to SQLite. No source documents are read in discovery, and there is no LLM.

The `links` table holds normalized, classified hyperlinks: `source_artifact_id`,
`raw_url`, `normalized_url`, `anchor_text`, the `target_system` / `target_type` /
`link_kind` classifications, `discovered_at`, `last_seen_at`, and a `status`
(`ACTIVE` or `STALE`). A link is a duplicate when `source_artifact_id +
normalized_url + anchor_text` already exists — re-running discovery refreshes
`last_seen_at` instead of inserting. Links no longer present in the latest
extraction are flagged `STALE` (never deleted); a reappearing link returns to
`ACTIVE`. `link_scan_runs` stores per-run statistics.

Classification vocabulary:

- `target_system`: `sharepoint`, `onedrive`, `confluence`, `jira`,
  `azure_devops`, `github`, `teams`, `email`, `local_file`, `external_web`,
  `unknown`.
- `target_type` (best effort): `document`, `wiki_page`, `work_item`,
  `repository`, `pull_request`, `meeting`, `message`, `channel`,
  `email_address`, `folder`, `unknown`.
- `link_kind`: `internal`, `external`, `local`, `email`, or `unknown`.
