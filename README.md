# Knowledge Catalog (Navigate)

A local-first professional knowledge platform that indexes selected folders **in
place** — it never moves, renames, or modifies your source files. It catalogs
documents (and source code) in SQLite, extracts text and links into a local
cache, discovers and classifies how everything connects, and consolidates it into
a reviewed, traceable **knowledge graph** you can explore with SPARQL, query with
a graph-grounded AI assistant, govern over time, and publish to RDF.

The CLI is `catalog` (with `navigate` as an alias).

## Design principles

- Source documents are never moved, renamed, or modified.
- The catalog is an index, not a document store — only extracted text and
  metadata are cached under `cache/`, and the SQLite database stays local under
  `data/`.
- Nothing is a **fact** without traceable evidence, and nothing is **trusted**
  until a human approves it. The AI assistant declines rather than hallucinates.
- Each layer (extraction, links, semantic, knowledge, graph, RDF, GraphRAG,
  governance, compliance) is a clean stage that builds on the one before it.

## Quickstart

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Choose folders to index in `config/sources.yml`, then run the pipeline end to
end:

```bash
catalog init-db          # create the local SQLite index
catalog scan             # index configured folders
catalog extract          # cache text + raw links
catalog discover-links   # normalize + classify links
catalog classify         # LLM proposes structured knowledge per document
catalog consolidate      # converge into cross-document knowledge objects
catalog knowledge-stats  # inspect the result
```

`classify` and `consolidate` need an LLM provider — set one up in
`config/llm.yml` (Claude, OpenAI, or a fully offline Ollama model). New here?
Start with **[Catalog your files](docs/how-to/catalog-your-files.md)**.

On a big corpus, `extract`, `discover-links`, and `classify` each take a
`--workers N` flag to run independent items in parallel (DB writes stay
single-threaded, so results are unchanged). Defaults live in
`config/performance.yml`.

## The pipeline at a glance

```
filesystem -> scanner -> SQLite catalog -> document cache -> link discovery
                                                  |
                                                  +--> semantic classification
                                                            |
                                                            +--> knowledge consolidation
                                                                      |
                                          +---------------------------+---------------------------+
                                          |               |                |          |          |
                                       graph/SPARQL   GraphRAG ask    RDF/Fuseki   governance  compliance
```

The scanner indexes `.docx`, `.pptx`, `.xlsx`, `.pdf`, `.md`, `.txt` and — with
**code-aware indexing** (on by default) — source code in many languages, parsed
with tree-sitter into `Module` / `Class` / `Function` / `Library` entities. Set
`index_code: false` in `config/sources.yml` to index documents only.

## What can I do with it?

Task-oriented recipes live under **[`docs/how-to/`](docs/how-to/README.md)**.

| I want to… | Guide |
| --- | --- |
| Index my folders and track what changed | [Catalog your files](docs/how-to/catalog-your-files.md) |
| Find and classify links inside documents | [Discover and classify links](docs/how-to/discover-and-classify-links.md) |
| Build a reviewed knowledge graph | [Build a knowledge graph](docs/how-to/build-a-knowledge-graph.md) |
| Search, navigate, and validate the graph (no LLM) | [Explore the knowledge graph](docs/how-to/explore-the-knowledge-graph.md) |
| Ask questions and get cited, graph-grounded answers | [Ask questions with GraphRAG](docs/how-to/ask-questions-with-graphrag.md) |
| Keep the graph trustworthy, owned, and fresh | [Govern your knowledge](docs/how-to/govern-your-knowledge.md) |
| Publish to RDF and query with SPARQL | [Publish to RDF and SPARQL](docs/how-to/publish-to-rdf-and-sparql.md) |
| Ground an AI coding agent in my codebase | [Ground an AI agent in your code](docs/how-to/ground-an-ai-agent-in-your-code.md) |
| Index a code repository | [docs/code-indexing.md](docs/code-indexing.md) |
| Assess compliance against standards and find gaps | [docs/compliance.md](docs/compliance.md) |

## Configuration

All configuration lives in `config/*.yml`; every file is optional and falls back
to sensible defaults. Real API keys go in your shell environment or an ignored
`.env` (copy `.env.example`), never in YAML.

| File | Controls | Used by |
| --- | --- | --- |
| `sources.yml` | folders to index, excludes, `index_code` | [Catalog your files](docs/how-to/catalog-your-files.md) |
| `extraction.yml` | fast vs. high-quality extraction | [Discover and classify links](docs/how-to/discover-and-classify-links.md) |
| `link_patterns.yml` | internal domains, system matching | [Discover and classify links](docs/how-to/discover-and-classify-links.md#4-optional-tell-navigate-which-domains-are-internal) |
| `llm.yml` | provider, chunking, adaptive routing | [Build a knowledge graph](docs/how-to/build-a-knowledge-graph.md#1-configure-an-llm-provider) |
| `pricing.yml` | token rates for cost reporting | [docs/llm-optimization.md](docs/llm-optimization.md) |
| `performance.yml` | parallel worker counts for `extract` / `discover-links` / `classify` | [docs/llm-optimization.md](docs/llm-optimization.md#latency-concurrent-classification) |
| `governance.yml` | freshness, quality, drift, cadence, agent-review policy | [Govern your knowledge](docs/how-to/govern-your-knowledge.md) |
| `jena.yml` | Fuseki endpoint / dataset | [Publish to RDF and SPARQL](docs/how-to/publish-to-rdf-and-sparql.md) |
| `compliance.yml` | control types, coverage, staleness | [docs/compliance.md](docs/compliance.md) |
| `api.yml` | REST API host/port/auth/flags | [docs/navigate-api.md](docs/navigate-api.md) |

## Integrations

- **REST API** — `catalog api` (or `navigate api`) serves a thin, local-first
  FastAPI over the same services as the CLI: artifacts, links, knowledge,
  governance, compliance, graph analytics, cost/usage, RDF projection, and
  GraphRAG reasoning. Interactive docs at <http://127.0.0.1:8000/docs>. See
  [docs/navigate-api.md](docs/navigate-api.md).
- **MCP server** — `catalog mcp` exposes the approved graph and the GraphRAG
  assistant as Model Context Protocol tools so an agent (Claude Code, Claude
  Desktop, …) can ground its reasoning in cited knowledge. With
  `--enable-agent-review` it also exposes opt-in, policy-gated write tools so an
  agent can approve high-confidence items — every decision tagged `agent:<name>`
  and reversible. See [docs/mcp.md](docs/mcp.md) and
  [Ground an AI agent in your code](docs/how-to/ground-an-ai-agent-in-your-code.md).
- **Docker** — a `Dockerfile` and `docker-compose.yml` package the API and an
  optional Apache Jena Fuseki triplestore (`docker compose up --build api`,
  `docker compose up -d fuseki`).

## Reference docs

| Doc | Topic |
| --- | --- |
| [docs/how-to/](docs/how-to/README.md) | task-oriented how-to guides (start here) |
| [docs/code-indexing.md](docs/code-indexing.md) | code-aware indexing with tree-sitter |
| [docs/classification-audit.md](docs/classification-audit.md) | how classification & consolidation decide |
| [docs/llm-optimization.md](docs/llm-optimization.md) | prompt caching, routing, cost reporting |
| [docs/compliance.md](docs/compliance.md) | standards, requirements, assessment, gaps |
| [docs/navigate-api.md](docs/navigate-api.md) | REST API client contract |
| [docs/mcp.md](docs/mcp.md) | MCP tool catalogue & client config |

## Architecture

The scanner is a small pipeline (`scanner → artifact queue → database`) with a
**scan event bus** that publishes one event per processed artifact, so future
processing subscribes to events instead of modifying the scanner:

```python
from catalog.scanner import Scanner
from catalog.events import ScanStatus

scanner = Scanner(db_path="data/catalog.sqlite")

def on_new_or_changed(event):
    print(event.status, event.artifact.path, event.artifact.id)

scanner.event_bus.subscribe(on_new_or_changed, statuses={ScanStatus.RAW, ScanStatus.CHANGED})
scanner.scan("config/sources.yml")
```

A failing subscriber is logged and isolated so it can never corrupt indexing.
Each later stage is an isolated package — `links/`, `semantic/`, `knowledge/`,
`graph/`, `rdf/`, `graphrag/`, `governance/`, `compliance/` — fed by the stage
before it.

## Development

```bash
pytest                                   # quiet run (configured in pyproject)
pytest --cov --cov-report=term-missing   # with a coverage summary
ruff check .                             # lint (enforced in CI)
ruff format .                            # format (not gated yet)
mypy                                     # advisory in CI
```

CI runs three jobs: `lint` (ruff enforced, mypy advisory), `test`
(pytest + coverage on Python 3.11 and 3.12), and `benchmark`. Run the CLI without
installing the package with `PYTHONPATH=src python -m catalog.cli stats`.

Contributing? See [CLAUDE.md](CLAUDE.md) for repository conventions, including the
rule to keep docs in sync with CLI/config changes.

## License

[MIT](LICENSE) © 2026 Kristoffer Isbak Thomsen
