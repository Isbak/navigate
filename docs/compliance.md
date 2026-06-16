# Compliance & standards layer

The compliance layer maps the organization's controls onto the requirements of
the standards it must follow, records an evidence-backed assessment of whether
each requirement is met, and answers *"are we compliant with X, and prove it?"*
and *"where are our gaps?"*.

It is built entirely on the platform's existing invariants — **traceable
evidence, human approval, decline-don't-hallucinate** — so it is an extension of
the knowledge graph rather than a parallel system.

## Vocabulary additions

Two object types join the existing ten (`knowledge.models.OBJECT_TYPES`):

| Type | Meaning | Example id |
| --- | --- | --- |
| `Standard` | A standard/regulation/policy family | `standard_iso_27001` |
| `Requirement` | One normative clause/article/control | `requirement_gdpr_art_32` |

Three predicates join `RELATIONSHIP_PREDICATES`:

| Predicate | Direction | Meaning |
| --- | --- | --- |
| `mandated_by` | `Requirement → Standard` | the requirement belongs to the standard |
| `satisfies` | `control → Requirement` | a control claims to meet the requirement |
| `supersedes` | `* → *` | an amended standard/requirement replaces an older one |

A **control** is not a new type: it is an existing `Capability`, `Process`,
`Platform`, or `Technology` object (configurable in `config/compliance.yml`).

## Data flow

```
standards docs ──classify (LLM)──► candidate_requirements ─┐
curated YAML/CSV ──compliance import──────────────────────┤
                                                          ├─► consolidate ─► Standard + Requirement objects
internal docs ──► Capability/Process/Platform objects ────┘                 + mandated_by edges
                                                                                   │
compliance-proof docs ──► satisfies edges (control → requirement) ─────────────────┤
                                                                                   ▼
                                                  compliance assess ─► compliance_assessments
                                                                                   │
                                                                                   ▼
                                       coverage • gaps • prove • CLI • API • SPARQL
```

Both ingestion paths write to one `candidate_requirements` table, so a single
`consolidate` turns curated and LLM-mined clauses into the same objects.
`consolidate` also creates the `Standard` objects and the `mandated_by` edges,
and (via `compliance.sync.sync_requirements`) enriches the
`compliance_standards` / `compliance_requirements` tables with the clause
locators and versions the generic object model cannot carry.

## Tables

All compliance tables soft-reference `knowledge_objects.id` **by value** (with an
index, no enforced foreign key) — the same pattern the governance tables use — so
that a `consolidate`, which drops and recreates `knowledge_objects`, never
destroys assessments, sign-offs, or their evidence.

- `compliance_standards` — authority, version, jurisdiction, effective date.
- `compliance_requirements` — clause ref, title, text, obligation level, the
  standard it belongs to, and the version it is assessed against.
- `compliance_assessments` — the sign-off record: requirement, control, derived
  `status`, `assessed_against_version`, rationale, assessor, and a
  `review_status` (`PROPOSED` → `APPROVED`/`REJECTED`). Unique per
  `(requirement, control)`.
- `compliance_assessment_evidence` — the quotes backing an assessment (cascade
  child of the assessment).
- `compliance_runs` — per-run statistics.

Clause/article locators ride on a new nullable `clause_ref` column on
`knowledge_evidence` and `compliance_assessment_evidence` — the legal-citation
analogue of the existing `page_number` / `slide_number`.

## Assessment lifecycle

`compliance.service.assess` evaluates each requirement:

1. Find the controls whose `satisfies` edge targets it (only **approved** edges
   count by default — the platform trust rule).
2. Gather each control's evidence and **derive** a status:
   - `SATISFIED` — an approved control with fresh, traceable evidence.
   - `PARTIAL` — a control exists with evidence but is unapproved, or its proof
     is stale (older than `stale_evidence_days`, or its governance freshness is
     `STALE`/`ARCHIVED`).
   - `GAP` — no control satisfies the requirement, or the control has no
     evidence.
   - `NOT_APPLICABLE` — only ever set by a human, never derived.
3. Write the assessment as `PROPOSED`, preserving any prior human review on a
   re-run (keyed on the `(requirement, control)` pair, like `consolidate`).

**The engine never concludes compliance on its own.** `assess` derives a status
but always leaves `review_status = PROPOSED`; `coverage`, `gaps`, and `prove`
count a requirement as met only once a human has **approved** the assessment.
The evidence invariant is enforced here too: a `SATISFIED`/`PARTIAL` assessment
must carry ≥ 1 evidence row.

`prove` walks `Requirement ←satisfies– control →(evidence)` and returns the cited
proof, or emits the platform's standard decline — **"No supporting evidence
found."** — when nothing approved and evidenced backs the requirement.

## Versioning

Amended standards are new artifacts (content-addressed ids) linked to the version
they replace with `supersedes`. Each assessment pins
`assessed_against_version`, so when a standard moves, an old sign-off is visibly
stale rather than silently wrong.

## Surfaces

- **CLI** — `catalog compliance {import, assess, standards, requirements,
  coverage, gaps, show, prove, assessments, approve, reject}`, plus
  `catalog ask "<requirement>" --prove`.
- **REST API** — `/api/compliance/{standards, requirements, coverage, gaps,
  assessments, prove/{requirement}}`, `POST /assessments/{id}/approve|reject`,
  and `POST /assess` (tracked job).
- **RDF** — `Requirement` resources carry `kg:clauseRef`, `kg:obligationLevel`,
  and (once approved) `kg:complianceStatus`; the predicates project as
  `kg:mandatedBy`, `kg:satisfies`, `kg:supersedes`.
- **SPARQL** — `queries/compliance_gaps.rq`, `compliance_coverage.rq`,
  `requirements_for_standard.rq`, `controls_satisfying.rq`.

## Configuration

`config/compliance.yml` (all keys optional):

```yaml
control_types: [Capability, Process, Platform, Technology]
assessment:
  coverage_threshold: 0.8        # reporting threshold for "covered"
  stale_evidence_days: 365       # downgrade SATISFIED -> PARTIAL beyond this
  require_approved_controls: true
```
