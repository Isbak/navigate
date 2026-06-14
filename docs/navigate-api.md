# Navigate REST API — contract notes for clients

This document describes the Navigate REST API from the point of view of a
client — primarily [`navigate-compass`](https://github.com/isbak/navigate) (the
"Compas" UI), but the contract is the same for any consumer. It complements the
endpoint table in the project [`README.md`](../README.md#rest-api) and the live
OpenAPI schema served at `/openapi.json`; it does not duplicate them.

The focus here is the part a client cannot learn from the OpenAPI schema alone:
**what the API deliberately does not expose, and how a client is expected to
degrade gracefully instead of inventing it.**

## Ground rules

- **Base path**: every endpoint lives under `/api` (for example
  `GET /api/stats`).
- **Pagination envelope**: every list endpoint returns
  `{items, limit, offset, total}`. `total` is the count for the *query*, so it
  is safe to paginate against.
- **Error envelope**: every failure returns `{error, message, details}` with the
  matching HTTP status. Clients should branch on the HTTP status, not on string
  matching the `message`.
- **Local-first and read-heavy**: the server binds to loopback by default, makes
  no external calls unless `enable_graphrag` / `enable_classify` are set, and
  `POST /api/ask` returns `501` until GraphRAG is enabled. Treat a `501` from
  `ask` as "feature off", not "broken".

## Graceful gaps

The API is intentionally a thin, stable projection of the existing
service/repository layer. Several things a dashboard UI might *want* are not
part of that projection. They are listed here so clients hide or derive them
rather than fabricating data that looks authoritative but is not.

For each gap: what is missing, why, and what Compas does instead.

### 1. No domains resource

**Missing.** There is no `GET /api/domains` (nor any `/api/domains/{id}`). A
client cannot enumerate the knowledge domains, their owners, or their per-domain
quality / freshness / backlog metrics through the API.

**What does exist.** `domain` is only a *filter*:
`GET /api/knowledge-objects?domain=<name>` narrows the object list. Domain
*analysis* lives behind the CLI and the governance dashboard
(`catalog.governance.domains`, `catalog.graph.domains`,
`queries/knowledge_domains.rq`), where domains are aggregated from document
classifications and the graph's object types — but none of that is surfaced as a
REST resource.

**Degrade gracefully.** Compas does not render a domains landing page, a domain
directory, or per-domain metric tiles. Where a domain filter control is useful,
it is populated lazily from the `domain`/`object_type` values present on objects
the client has already loaded — it is treated as "the domains seen so far", not
as a canonical, exhaustive list. Compas never synthesizes per-domain owners or
scores client-side.

### 2. No knowledge-growth trend

**Missing.** There is no time series of how the catalog grew. `GET /api/stats`
returns point-in-time totals (`artifact_count`, `knowledge_object_count`,
`relationship_count`, …) and a single `last_scan` object — not a history of
those counts over time. No endpoint returns "objects added per day/week".

**Why.** The index is regenerable and counts are snapshots; the API does not
keep a metrics history table to back a trend line.

**Degrade gracefully.** Compas shows the `stats` totals as plain numbers / KPI
cards. It does not draw sparklines, growth curves, or "+N this week" deltas, and
it does not infer a trend from a single snapshot. If growth ever matters, it is
left blank or labelled "not tracked" rather than interpolated.

### 3. No change-log / activity feed

**Missing.** There is no feed of recent content changes — newly added objects,
approvals/rejections, edits, merges. There is no `GET /api/changes`,
`/api/activity`, or audit stream.

**What does exist (and is not the same thing).** `GET /api/jobs` lists
*pipeline executions* (scan, extract, classify, …) with their status and
timing. That is operational job history, not a content-level change log, and
clients should not present it as "recent activity in the knowledge base".

**Degrade gracefully.** Compas omits any "recent activity" / changelog panel.
When a recency hint is genuinely needed, it falls back to ordering a specific
resource by its own `updated_at` / `created_at` timestamps (objects,
relationships, evidence all carry them) rather than presenting a unified,
cross-resource feed that the API cannot back.

### 4. No per-row child counts

**Missing.** List rows do not carry aggregate counts of their children. A
`KnowledgeObject` in `GET /api/knowledge-objects` has no `relationship_count`,
`evidence_count`, or `mention_count`. To learn how many relationships an object
has, a client must call `GET /api/knowledge-objects/{id}/relationships` (and the
`/evidence`, `/mentions` siblings). Those sub-resource endpoints return the full
list in one shot, so their `total` equals the page length — they are a fetch,
not a cheap count.

**Degrade gracefully.** Compas does not show count badges in list / table views,
because doing so would mean an N+1 fan-out of requests per page. Counts are
fetched lazily, only when a row is expanded or a detail view is opened, and the
badge is simply absent until then.

**Use the counts that *are* there.** Two payloads already include the relevant
counts, and clients should prefer them instead of fanning out:

- **Quality** (`GET /api/governance/quality`): each `QualityItem` carries
  `evidence_count` and `document_count`.
- **Graph nodes** (`GET /api/graph/nodes`, `/api/graph/export-json`): each
  `GraphNode` carries `documents` and `mentions`.

## Why this is the right default

Inventing a domains list, a growth curve, a change feed, or per-row counts on
the client would produce numbers that look authoritative but drift from the
catalog. Because the catalog is the source of truth and the API is a deliberately
thin projection of it, the honest client behavior is to **render what the API
returns and visibly omit the rest** — which is exactly what Compas does. If any
of these become first-class needs, the fix is to add the resource to the API (so
every client benefits and stays consistent), not to reconstruct it per client.
