# How-to guides

Task-oriented guides for getting things done with Navigate (the `catalog` /
`navigate` CLI). Each guide is a recipe: a goal, the prerequisites, and the
exact commands in order. For *why* a layer works the way it does, follow the
"How it works" pointer at the end of each guide to the matching reference doc.

New here? Start with **[Catalog your files](catalog-your-files.md)** and work
down the "Build the graph" rows — that is the core pipeline end to end.

## Get started

| I want to… | Guide |
| --- | --- |
| Index my folders and see what changed | [Catalog your files](catalog-your-files.md) |
| Run the whole pipeline in five minutes | [Quickstart in the README](../../README.md#quickstart) |

## Build the graph

| I want to… | Guide |
| --- | --- |
| Find and classify the links inside my documents | [Discover and classify links](discover-and-classify-links.md) |
| Turn documents into a reviewed knowledge graph | [Build a knowledge graph](build-a-knowledge-graph.md) |
| Index a code repository, not just documents | [docs/code-indexing.md](../code-indexing.md#how-to-use) |
| Audit how classification and consolidation decided things | [docs/classification-audit.md](../classification-audit.md) |
| Keep LLM cost and latency down | [docs/llm-optimization.md](../llm-optimization.md) |

## Explore & ask

| I want to… | Guide |
| --- | --- |
| Search, navigate, and validate the graph (no LLM) | [Explore the knowledge graph](explore-the-knowledge-graph.md) |
| Ask questions and get cited, graph-grounded answers | [Ask questions with GraphRAG](ask-questions-with-graphrag.md) |

## Govern & publish

| I want to… | Guide |
| --- | --- |
| Keep the graph trustworthy, owned, and fresh | [Govern your knowledge](govern-your-knowledge.md) |
| Publish to RDF and query it with SPARQL | [Publish to RDF and SPARQL](publish-to-rdf-and-sparql.md) |
| Assess compliance against standards and find gaps | [docs/compliance.md](../compliance.md) |

## Integrate

| I want to… | Guide |
| --- | --- |
| Ground an AI coding agent in my codebase | [Ground an AI agent in your code](ground-an-ai-agent-in-your-code.md) |
| Expose the platform over MCP tools | [docs/mcp.md](../mcp.md) |
| Consume the platform over a REST API | [docs/navigate-api.md](../navigate-api.md) |

## Troubleshooting

Something not working? Run the built-in health check first:

```bash
catalog doctor            # OK/WARN/FAIL report across your whole setup
catalog doctor --strict   # exit non-zero on warnings too (useful in CI)
catalog doctor --json     # machine-readable report
```

`doctor` verifies that your `config/*.yml` files parse, the database connects and
is writable, the cache directory is usable, the active LLM provider's API key is
set, the Fuseki endpoint is reachable (only needed for `fuseki-load` /
`fuseki-clear`), and that the API isn't about to bind to all interfaces without
an API key. Each check reports `OK`, `WARN`, or `FAIL`; see `catalog doctor
--help`. For API exposure specifically, see
[the API security notes](../navigate-api.md#security).
