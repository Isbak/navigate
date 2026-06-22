# Build a knowledge graph

**Goal:** turn your indexed documents into a reviewed knowledge graph — reusable,
cross-document knowledge objects (capabilities, decisions, risks, platforms, …)
linked by typed relationships, each one traceable to supporting evidence.

This is a two-step LLM pipeline followed by a human review loop:

```
classify (per document)  →  consolidate (cross-document objects)  →  review & approve
```

## Prerequisites

- You have scanned and extracted your files — see
  [Catalog your files](catalog-your-files.md) and
  [Discover and classify links](discover-and-classify-links.md).
- An LLM provider configured (next section).

## 1. Configure an LLM provider

Keep shareable, non-secret settings in `config/llm.yml`; put real API keys in
your shell environment or an ignored `.env` (copy from `.env.example`) — never in
the YAML.

```yaml
provider: claude

ollama:
  model: qwen3:14b
  host: http://localhost:11434

claude:
  model: claude-sonnet-4-5
  api_key_env: ANTHROPIC_API_KEY  # value read from env/.env, not YAML
  prompt_cache: true

openai:
  model: gpt-5.5
  api_key_env: OPENAI_API_KEY

max_input_chars: 12000      # size of one chunk sent to the model
chunk_overlap: 500          # overlap between consecutive chunks
max_chunks: 20              # cap on chunks per document (bounds cost)

routing:                    # adaptive model routing
  enabled: true
  fast_model: claude-haiku-4-5
  deep_model: claude-sonnet-4-5
```

Three providers ship: `OllamaProvider` (fully offline), `ClaudeProvider`, and
`OpenAIProvider`. Pick the active one with `provider:` (override per command with
`--llm-config`). To keep cost and latency low, see
[docs/llm-optimization.md](../llm-optimization.md).

## 2. Classify documents

Classification proposes structured knowledge per document — document type,
domains, summaries, and candidate entities/capabilities/decisions/risks/
relationships — every item carrying a confidence, its provenance, and a
`review_status` of `NEW`. It never creates facts.

```bash
catalog classify                          # classify all changed/new documents
catalog classify --artifact-id doc_abc123 # one document
catalog classify --force                  # reclassify everything
```

Then review the discovery analytics without re-reading every document:

```bash
catalog classification-stats
catalog show-summary --artifact-id doc_abc123
catalog show-decisions [--min-confidence 0.7]
catalog show-risks
catalog show-capabilities
catalog show-relationships
```

Documents are only (re)classified when their extraction changed, their
classification is missing, or `--force` is passed, so re-runs are cheap.

## 3. Consolidate into knowledge objects

Consolidation converges the per-document proposals into single, cross-document
knowledge objects with stable, URI-ready ids and typed relationships:

```bash
catalog consolidate                       # build knowledge objects from semantic data
catalog consolidate --use-llm             # use the LLM for borderline merge suggestions
catalog consolidate --min-confidence 0.5  # raise the noise floor (default 0.3)
catalog consolidate --force               # rebuild, discarding prior review decisions
catalog consolidate --all-sources         # ignore the source-folder scope (legacy)
```

A normal `consolidate` rebuilds derived data from scratch but re-applies prior
human decisions (ids are stable); `--force` discards them. Low-confidence,
one-off proposals are dropped before clustering by the **noise floor**
(`min_mention_confidence`, default `0.3`).

To permanently remove all material for a file or folder:

```bash
catalog clean-source --path PATH                       # purge + reconsolidate
catalog clean-source --path PATH --no-reconsolidate    # purge without rebuilding
```

## 4. Review and approve

Objects and relationships start as `PROPOSED`. **Only `APPROVED` items are
trusted** by the graph, GraphRAG, and RDF layers.

```bash
catalog review-candidates             # PROPOSED objects/relationships + duplicates
catalog approve-object <id>
catalog reject-object <id>
catalog approve-relationship <id>     # id from review-candidates / show-object
catalog reject-relationship <id>
```

## 5. Inspect the graph

```bash
catalog knowledge-stats               # top capabilities/concepts/technologies, conflicts, duplicates
catalog knowledge-growth              # growth trend (new + cumulative) by month
catalog knowledge-growth --interval week --limit 8
catalog show-object capability_release_governance
catalog search-knowledge "release"
catalog export-graph-json             # writes exports/graph/{nodes,edges}.json
```

## Next step

- Navigate and validate the graph: [Explore the knowledge graph](explore-the-knowledge-graph.md)
- Ask cited questions: [Ask questions with GraphRAG](ask-questions-with-graphrag.md)
- Keep it trustworthy over time: [Govern your knowledge](govern-your-knowledge.md)

---

## How it works (data model)

**Entity resolution.** Consolidation groups entity proposals that refer to the
same thing using, in order of strength: case/punctuation/whitespace
normalization, fuzzy matching (token-set + character-trigram Dice with a
containment boost), and — with `--use-llm` — an LLM yes/no judgement on
borderline pairs. Each cluster records a **merge confidence**; pairs below the
auto-merge threshold surface as *duplicate candidates* rather than being merged.

**Evidence invariant.** No knowledge object exists without at least one
`knowledge_evidence` row (a supporting quote). **Scoring** blends five signals —
distinct documents, mention count, relationship consistency, average LLM
confidence, and review history — so breadth beats repetition.

Tables:

- `knowledge_objects` — consolidated objects: stable URI-ready `id`, `name`,
  `object_type` (Capability, Initiative, Technology, Platform, Team, Product,
  Concept, Decision, Risk, Process), `description`, `canonical_name`,
  `confidence`, `status`, `merge_confidence`, timestamps.
- `knowledge_mentions` — every (object, document) occurrence with confidence and
  source text.
- `knowledge_evidence` — traceable quotes with optional page/slide locators.
- `knowledge_relationships` — `source predicate target` triples with confidence,
  evidence, and a `review_status`.
- `knowledge_reviews` — the audit trail of review actions, used by scoring.

Everything here is fully regenerable from the semantic `candidate_*` tables via
`catalog consolidate`. The upstream semantic layer only ever writes
`OBSERVATION` and `HYPOTHESIS` rows — never `FACT`. For the full audit of how
classification and consolidation decide things, see
[docs/classification-audit.md](../classification-audit.md).

### Source-folder scope

Consolidation only considers documents that currently live under a source folder
in `config/sources.yml` (curated standard imports, which have no path, always
count). Drop a folder and re-consolidate: objects sourced solely from it
disappear from the graph while their raw `candidate_*` rows stay in the database,
so re-adding the path brings them back. `--all-sources` opts out;
`clean-source` removes material permanently.
