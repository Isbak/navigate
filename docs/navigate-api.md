# Navigate REST API — contract notes for clients

This document describes the Navigate REST API from the point of view of a
client — primarily [`navigate-compass`](https://github.com/isbak/navigate) (the
"Compas" UI), but the contract is the same for any consumer. It complements the
[integrations overview in the project `README.md`](../README.md#integrations) and
the live OpenAPI schema served at `/openapi.json`; it does not duplicate them.

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

## Closing the gaps

Earlier, four things a dashboard UI wants were not part of the projection, and
clients had to hide or derive them. They are now first-class. Each is a thin
projection of an existing service/repository function (no business logic in the
route), and each has a matching CLI command, so the API and the `catalog` CLI
stay in lockstep.

### 1. Domains resource

A knowledge domain is a business area (Digital Transformation, Architecture,
Test & Release, …). Objects belong to the domains their source documents were
classified under; the domain's metrics are aggregated over its objects.

| | |
|-|-|
| List | `GET /api/governance/domains` → `[DomainHealth]` |
| Detail | `GET /api/governance/domains/{name}` → `DomainHealth` (404 if unknown) |
| CLI | `catalog governance domains` |

`DomainHealth` = `{domain, owner, object_count, avg_quality, avg_freshness,
review_backlog}`. Every *configured* domain is returned even with zero objects,
so an uncovered domain is itself visible (a governance signal). The owner comes
from `config/governance.yml`.

### 2. Knowledge-growth trend

A time series of how the catalog grew, bucketed by period.

| | |
|-|-|
| Endpoint | `GET /api/governance/growth?interval=month&limit=12` → `GrowthTrend` |
| Params | `interval` ∈ `day` \| `week` \| `month` (default `month`); `limit` periods (default 12) |
| CLI | `catalog knowledge-growth --interval month --limit 12` |

`GrowthTrend` = `{interval, points: [GrowthPoint]}` where each `GrowthPoint` is
`{period, artifacts_added, artifacts_total, objects_added, objects_total,
relationships_added, relationships_total}`. `*_added` is new in the period;
`*_total` is the cumulative count. Cumulative totals are computed over the full
history first and *then* windowed to the last `limit` periods, so the totals
stay correct under windowing. Periods with no new rows are omitted, but the
cumulative total carries forward across the gap. Rows whose creation timestamp
is missing or unparseable contribute to no period.

### 3. Change-log / activity feed

The governance change-log (audit trail): objects added/removed, confidence and
freshness transitions, relationships added/removed, review decisions, and drift
findings — newest first.

| | |
|-|-|
| Endpoint | `GET /api/governance/changes` → `PaginatedResponse[ChangeLogEntry]` |
| Filters | `object_id`, `change_type`, plus `limit` / `offset` |
| CLI | `catalog governance changes` (and `catalog governance history <id>` for one object) |

`ChangeLogEntry` = `{id, change_type, target_kind, object_id, field, old_value,
new_value, detail, detected_at}`. The feed is populated by `catalog governance
scan` (and review actions), so run a scan to keep it current. This is distinct
from `GET /api/jobs`, which is pipeline-execution history, not content changes.

### 4. Per-row child counts

List rows now carry their child counts, so a table view can show badges without
an N+1 fan-out to the sub-resources.

`GET /api/knowledge-objects` (and the detail endpoint) include, on every row,
`relationship_count`, `evidence_count`, and `mention_count`. The dedicated
sub-resource endpoints (`.../relationships`, `.../evidence`, `.../mentions`)
remain the way to fetch the actual rows. The CLI already surfaces these counts
via `catalog show-object` and `catalog knowledge-stats`.

## Further capability exposure

The same "project an existing service function" pattern was used to surface
several mature CLI-only capabilities through the API. Each group is a thin
wrapper over the service/repository the matching `catalog` command already uses,
so the contract is best read from the live `/openapi.json`; the summary below is
a map, not a field-by-field spec.

### Cost / LLM usage — `cost` tag

Read-only projection of the recorded `llm_usage` ledger (no LLM call is made).

| Endpoint | Returns | CLI |
|-|-|-|
| `GET /api/cost/summary` | overall tokens & spend | `catalog cost-report` |
| `GET /api/cost/by-operation` | spend grouped by operation | `catalog cost-report` |
| `GET /api/cost/by-model` | spend grouped by model | `catalog cost-report` |
| `GET /api/cost/per-document?top=N` | most expensive documents | `catalog cost-report` |
| `GET /api/cost/vs-quality?top=N` | spend beside classification confidence | `catalog cost-report` |

### Graph analytics — `graph` tag

Computed over the approved graph (same NetworkX path the CLI uses; no Fuseki).

| Endpoint | Returns | CLI |
|-|-|-|
| `GET /api/graph/health` | islands, untraceable claims, low-confidence, duplicates, connectivity | `catalog graph health` |
| `GET /api/graph/metrics?top=N` | density, components, clusters, centrality rankings | `catalog graph metrics` |
| `GET /api/graph/domains` | per-object-type domains with most-central concepts | `catalog graph domains` |
| `GET /api/graph/export-gexf` | GEXF (Gephi) document | `catalog graph export-gexf` |
| `GET /api/graph/export-graphml` | GraphML (yEd/Cytoscape/Neo4j) document | `catalog graph export-graphml` |

The two export endpoints return the serialised document as a download
(`Content-Disposition` attachment), not the JSON envelope.

### Governance extras — `governance` tag

| Endpoint | Returns / does | CLI |
|-|-|-|
| `GET /api/governance/drift?limit=N` | `[ChangeLogEntry]` drift findings, newest first | `catalog governance drift` |
| `GET /api/governance/owners` | `[OwnerAssignment]` (object → Team/Person/Domain) | `catalog governance owners` |
| `GET /api/governance/objects/{id}/history` | combined audit view (changes + lifecycle + owner) | `catalog governance history <id>` |
| `POST /api/governance/objects/{id}/assign-owner` | assign an owner (`{owner_type, owner_id}`) | `catalog governance assign-owner` |
| `POST /api/governance/objects/{id}/flag` | flag as `NEEDS_ATTENTION` | `catalog governance flag` |

`assign-owner` rejects an unknown `owner_type` with `400`; both write endpoints
return `ActionResponse` and `404` when the object is unknown.

### Agent review & undo — `governance` tag

Policy-bounded agent approval and its human undo. The policy (confidence window,
evidence requirement, type/predicate allowlists, per-run cap) comes from the
`agent_review` block of `config/governance.yml`; request fields only *narrow* it.
Decisions are tagged `agent:<name>` and are reversible.

| Endpoint | Body / does | CLI |
|-|-|-|
| `POST /api/governance/agent-approve` | `{target, agent?, min_confidence?, max_confidence?, note, dry_run}` → `AgentApproveResponse` (counts + candidates) | `catalog governance agent-approve` |
| `POST /api/governance/revert` | `{target_kind, target_id, note}` → `RevertResponse` (undo one decision) | `catalog governance revert` |
| `POST /api/governance/revert-agent` | `{agent?, since?, note}` → `RevertAgentResponse` (undo a batch) | `catalog governance revert-agent` |

`agent-approve` with `dry_run: true` writes nothing and returns the candidate
list. `revert-agent` never overrides a decision a human made after the agent. The
same policy and tags back the opt-in MCP write tools (see [docs/mcp.md](mcp.md)).

### RDF projection — `rdf` tag

| Endpoint | Returns | CLI |
|-|-|-|
| `GET /api/rdf/stats` | counts an export would contain (approved only) | `catalog rdf-stats` |
| `GET /api/rdf/export?fmt=turtle` | serialised RDF (turtle / json-ld / nt) as a download | `catalog rdf-export` |
| `GET /api/rdf/validate` | per-file re-parse of a prior `rdf-export` | `catalog rdf-validate` |

`export` serialises in memory and is side-effect free; `validate` reports on
whatever `rdf-export` previously wrote under `exports/rdf/` (empty if it has not
been run). An unsupported `fmt` returns `400`.

### GraphRAG reasoning modes — `ask` tag

The single-shot reasoning modes that join `POST /api/ask`. Each is gated by
`enable_graphrag` exactly like `ask` (returns `501` when off) and returns the
same `AskResponse` shape (answer, confidence, citations, optional context).

| Endpoint | Body | CLI |
|-|-|-|
| `POST /api/ask/explain` | `{term, depth, …}` | `catalog explain` |
| `POST /api/ask/impact` | `{term, depth, …}` | `catalog impact` |
| `POST /api/ask/compare` | `{term_a, term_b, depth, …}` | `catalog compare` |
| `POST /api/ask/path-reason` | `{term_a, term_b, depth, …}` | `catalog path-reason` |

## The principle still holds

The API remains a deliberately thin projection of the catalog, which is the
single source of truth. The right way to add a capability is to project it from
an existing service function so every client benefits and stays consistent —
which is exactly how the four resources above were added. A client should still
render what the API returns and visibly omit anything genuinely out of scope,
rather than fabricating numbers on the client that would drift from the catalog.
