# Ask questions with GraphRAG

**Goal:** ask natural-language questions and get answers that are *reasoned* over
your approved knowledge graph and backed by citations — objects, evidence quotes,
and a confidence band. The graph drives retrieval; the LLM only reasons over what
the graph supplies, so nothing unapproved can ever reach the model.

This is deliberately **not** naive RAG: there is no document search, no full-text
scan, no vector database, and no embedding retrieval. Graph retrieval is
mandatory.

## Prerequisites

- A graph with **approved** objects and relationships — see
  [Build a knowledge graph](build-a-knowledge-graph.md).
- An LLM provider configured in `config/llm.yml` (same abstraction as the
  semantic layer; select with `--llm-config`). For a fully offline setup, point
  it at Ollama.

## Ask a question

```bash
catalog ask "What supports Release Governance?"
catalog ask "What capabilities depend on Salesforce?" --depth 3
catalog ask "What decisions affect Test & Release?" --model qwen3:14b
catalog ask "What risks affect Salesforce?" --show-context --show-sparql --show-evidence
```

- `--depth` controls how far the graph neighbourhood is expanded (1, 2, or 3;
  **default 2**).
- `--model` overrides the configured model for one question.
- `--show-context` / `--show-sparql` / `--show-evidence` reveal exactly what was
  retrieved and how — useful for trust and debugging.

By default the assistant runs SPARQL against the in-memory projection built from
SQLite (no Fuseki required); `--fuseki` reroutes the same SPARQL to a live
endpoint.

## Purpose-built question shapes

```bash
catalog explain "Release Governance"                    # description, connections, evidence
catalog compare "Release Governance" "Platform Governance"
catalog impact "Salesforce"                             # capabilities/decisions/risks/teams affected
catalog path-reason "Release Governance" "Salesforce"   # retrieve the path, LLM explains it
```

## Follow-up questions

A session remembers each turn's question and retrieved objects, so referents
resolve:

```
Q1: "What supports Release Governance?"
Q2: "What risks are associated with that?"     # "that" -> Release Governance
```

## When there is no answer

If no object matches or no evidence is retrieved, the assistant declines
**before** calling the model and replies exactly:

```
No supporting evidence found.
```

This is the hallucination control working as designed — it answers only from
retrieved graph context, never invents objects/relationships/quotes, and every
answer carries its citations (`[E1]`) and a High / Medium / Low confidence band
computed from the retrieval, not the model's self-assessment.

## Next step

To expose this same Q&A to an AI coding agent over MCP, see
[Ground an AI agent in your code](ground-an-ai-agent-in-your-code.md).

---

## How it works

```
Question
  -> Intent analysis    (search object, type, relationship, reasoning type — no LLM)
  -> Graph retrieval    (match objects, expand neighbourhood via SPARQL)
  -> Evidence retrieval (approved relationships + supporting quotes)
  -> Context builder    (compact, deterministic, traceable context)
  -> LLM                (reasoning only, over the supplied context)
  -> Traceable answer   (objects + relationships + evidence + confidence)
```

Intent is parsed deterministically into a search object, object type,
relationship focus, and a **reasoning type** — `lookup`, `path`, `impact`,
`evidence`, `domain`, or `comparison` — which shapes both retrieval and the
prompt, leaving the final reasoning step as the only non-deterministic part.

Every answered question logs (at `-v`) its reasoning type, the counts of objects,
relationships, and evidence retrieved, the prompt size, the response time, and
the confidence band. `ask` token usage is priced into `catalog cost-report`.
