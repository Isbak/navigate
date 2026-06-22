# Govern your knowledge

**Goal:** turn the consolidated graph from a periodically rebuilt artifact into a
continuously governed platform you can trust — every object owned, reviewed,
scored for quality, and tracked for freshness and drift.

Governance is pure governance over the SQLite system of record: no retrieval, no
GraphRAG, no vectors. It runs out of the box (every config value has a sensible
default).

## Prerequisites

- A consolidated graph — see [Build a knowledge graph](build-a-knowledge-graph.md).

## 1. Run a governance scan

Each scan refreshes the freshness lifecycle, diffs the graph against the previous
one, scores quality, detects drift, and regenerates alerts:

```bash
catalog governance scan
```

## 2. See the state of the graph

```bash
catalog governance dashboard            # knowledge health at a glance
catalog governance review-queue         # objects awaiting review
catalog governance stale                # stale / aging knowledge
catalog governance quality              # quality scores (--ascending for worst first)
catalog governance orphaned             # objects/relationships/evidence missing their links
catalog governance alerts               # open alerts (--type to filter)
catalog governance drift                # detected knowledge drift
catalog governance changes              # recent audit-trail entries
catalog governance domains              # per-domain governance health
```

## 3. Act on objects

Approving an object pins its consolidation status so it flows into the RDF
projection; rejecting or archiving removes it.

```bash
catalog governance approve <object>     # trusted, exported to RDF
catalog governance archive <object>     # retired, kept for history
catalog governance reject <object>      # not trusted, excluded
catalog governance flag <object>        # mark as needing attention
catalog governance assign-owner <object> <Team|Person|Domain> "<owner>"
catalog governance owners               # ownership assignments
catalog governance history <object>     # full provenance + change history of one object
```

## 4. Let an agent approve the easy decisions (optional)

For a large backlog, you can delegate the high-confidence, low-risk approvals to
an agent while a human keeps control. `agent-approve` only touches `PROPOSED`
items that fall inside the configured policy (confidence window, evidence
required, optional type/predicate allowlists), capped per run. Every decision is
tagged `agent:<name>` so it stays attributable — and reversible.

```bash
catalog governance agent-approve --dry-run                 # preview the candidates
catalog governance agent-approve --target all --note "nightly pass"
catalog governance agent-approve --target relationships --min-confidence 0.9
```

The policy lives in the `agent_review` block of `config/governance.yml`; `--agent`,
`--min-confidence`, and `--max-confidence` override it for a single run. The same
guard rails back the opt-in MCP write tools — see [the MCP guide](../mcp.md#write-tools-opt-in-policy-gated).

**Undo.** Because agent decisions are tagged, a human can roll them back:

```bash
catalog governance revert <object-or-relationship-id>      # undo one decision
catalog governance revert-agent --agent agent --since 2026-06-22  # undo a batch
```

`revert` restores the prior review state (recorded as a new, human-attributed
audit event); `revert-agent` rolls back a whole batch by agent name / time window
and **never overrides a decision a human made after the agent**.

## 5. Export reports

```bash
catalog governance export   # quality_report.json, governance_report.json,
                            # knowledge_health.json, change_log.json
```

## 6. Run the whole pipeline on a cadence

`governance ingest` runs `scan → extract → discover-links → consolidate →
rdf-export → governance scan` on a schedule. A last-run marker drives the
cadence; `--force` runs regardless, and a failing step is recorded without
aborting the run.

```bash
catalog governance ingest [--schedule daily|weekly|manual] [--force]
```

## Configuration

Rules live in `config/governance.yml` — freshness thresholds, quality weights,
drift sensitivity, and the ingestion cadence. Every value falls back to a default.
Domains are **discovered** from documents' semantic classifications, not
authored by hand. They are then cleaned up by `config/domains.yml` — a
confidence floor, a fuzzy merge of near-duplicate names, and an optional
canonical taxonomy — so a single dense document does not surface a dozen
overlapping domains. See
[Build a knowledge graph](build-a-knowledge-graph.md#2-classify-documents).

## Related

- Publish approved, governed knowledge: [Publish to RDF and SPARQL](publish-to-rdf-and-sparql.md)
- Assess compliance against standards: [docs/compliance.md](../compliance.md)

---

## How it works (data model)

Six tables hold curated governance state. They reference object ids *softly* (by
value, not an enforced foreign key), so they **survive a `consolidate`** — which
deletes and recreates `knowledge_objects` — and ownership, review decisions, and
freshness history are never lost:

| Table | Purpose |
| --- | --- |
| `knowledge_owners` | who owns each object (Team / Person / Domain) |
| `knowledge_lifecycle` | freshness + review-workflow state, `last_seen_at`, history |
| `knowledge_quality` | the latest 0–100 quality score and its factors |
| `knowledge_alerts` | generated operator alerts |
| `knowledge_change_log` | the append-only audit trail |
| `knowledge_reviews` | the human review-action audit trail (reused) |

**Freshness lifecycle** (configurable in `config/governance.yml`):

```
seen recently            -> FRESH
no evidence for 180 days -> AGING
no evidence for 365 days -> STALE
archived by a reviewer   -> ARCHIVED
```

A continuous `freshness_score` decays linearly from 1.0 (seen today) to 0.0 at
the archive horizon and feeds the quality score.

**Quality scoring** gives every object a 0–100 score blending six factors:
evidence count, review status, freshness, relationship consistency, whether an
owner is assigned, and confidence.

**Change & drift.** Each scan diffs against the previous graph and appends to the
audit trail (new/removed objects and relationships, confidence/ownership changes,
freshness transitions). Drift detection flags disappearing evidence, established
objects vanishing, and terminology changes. Objects move through the review
workflow `PENDING_REVIEW → NEEDS_ATTENTION → APPROVED → ARCHIVED → REJECTED`.

**Agent vs human decisions.** Review actions carry a `reviewer` string. Human
actions use `cli` / `api` / a person's name; agent actions use `agent:<name>`.
They share the `APPROVED` state (an agent approval is a real approval), but the
tag makes agent decisions filterable and lets `revert-agent` undo a batch without
touching anything a human later decided.
