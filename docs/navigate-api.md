# Navigate REST API — contract notes for clients

This document describes the Navigate REST API from the point of view of a
client — primarily [`navigate-compass`](https://github.com/isbak/navigate) (the
"Compas" UI), but the contract is the same for any consumer. It complements the
endpoint table in the project [`README.md`](../README.md#rest-api) and the live
OpenAPI schema served at `/openapi.json`; it does not duplicate them.

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

## The principle still holds

The API remains a deliberately thin projection of the catalog, which is the
single source of truth. The right way to add a capability is to project it from
an existing service function so every client benefits and stays consistent —
which is exactly how the four resources above were added. A client should still
render what the API returns and visibly omit anything genuinely out of scope,
rather than fabricating numbers on the client that would drift from the catalog.
