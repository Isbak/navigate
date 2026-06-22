# Ground an AI agent in your code

**Goal:** as a developer, give your AI coding agent (Claude Code, Claude Desktop,
…) a grounded, cited map of your codebase. Instead of free-associating, the agent
queries a reviewed knowledge graph of your repo — its modules, the services and
APIs each talks to, dependencies, and known risks — and gets answers backed by
real evidence.

This recipe builds a code knowledge graph from a repository, then exposes it over
MCP so an agent can ground its reasoning in it.

## Prerequisites

- Python 3.11+ with the package installed, plus the MCP extra:

  ```bash
  pip install -e '.[mcp]'
  ```

- An LLM provider configured in `config/llm.yml` (see
  [Build a knowledge graph](build-a-knowledge-graph.md#1-configure-an-llm-provider)).
  The graph-first tools work fully offline; only the `ask` tool needs a provider.

## 1. Point a source at your repository

Code-aware indexing is **on by default**. Add the repo to `config/sources.yml`:

```yaml
sources:
  - path: "~/code/my-service"
    source_system: "git_repo"

# index_code: true is the default; set it false to index documents only.
```

Source files are parsed with [tree-sitter](https://tree-sitter.github.io/):
chunked along function/class boundaries (never mid-function), with imports,
classes, and functions read off the syntax tree as `Module`, `Class`,
`Function`, and `Library` entities linked by `defines` / `imports`. They are
classified with a code-specific prompt that captures each module's purpose, the
services/APIs it talks to, and any security or design risks. Vendored and build
directories (`node_modules`, `.venv`, `dist`, `build`, `target`, …) are excluded
automatically. The tree-sitter grammars ship in the `code` extra (included in
`.[dev]`); a language without an installed grammar degrades gracefully to
character-based chunking.

## 2. Build and review the graph

The same pipeline as documents applies to code:

```bash
catalog scan
catalog extract
catalog classify
catalog consolidate
```

Then review and approve what you trust — the MCP tools only read the **approved**
graph:

```bash
catalog review-candidates
catalog approve-object <id>
catalog approve-relationship <id>
```

(Full detail: [Build a knowledge graph](build-a-knowledge-graph.md). For the
code-specific walkthrough, see
[docs/code-indexing.md](../code-indexing.md#how-to-use).)

## 3. Start the MCP server

```bash
catalog mcp --db data/catalog.sqlite     # stdio server
catalog mcp --no-graphrag                 # graph-only, fully offline (no ask tool)
```

## 4. Connect your agent

For Claude Code:

```bash
claude mcp add navigate-knowledge -- catalog mcp --db /abs/path/to/data/catalog.sqlite
```

Or a raw client config block:

```json
{
  "mcpServers": {
    "navigate-knowledge": {
      "command": "catalog",
      "args": ["mcp", "--db", "/abs/path/to/data/catalog.sqlite"]
    }
  }
}
```

Use an **absolute** `--db` path so the tools resolve the same database regardless
of the client's working directory. Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`)
in the client's environment to enable the `ask` tool.

## What the agent can do

The server publishes seven tools. Six are deterministic, offline, and need no API
key; `ask` is the one LLM-backed tool (it degrades gracefully to
`{"available": false}` when no provider is configured):

| Tool | Needs LLM? | What the agent gets |
| --- | :---: | --- |
| `search_knowledge` | no | objects whose label/description match a term |
| `get_object` | no | one object's type, description, confidence, evidence count |
| `neighbors` | no | directly connected objects grouped by relationship |
| `impact` | no | what a change may affect, grouped by object type |
| `find_path` | no | shortest relationship path between two objects |
| `evidence_for` | no | supporting evidence quotes (artifact, quote, confidence) |
| `ask` | yes | a graph-first, cited answer with a confidence band |

Why this beats free-association: retrieval is **graph-first and mandatory**, only
**approved** knowledge is exposed, and every answer is traceable to evidence — so
"what calls the billing API?" or "what would changing this module affect?" comes
back grounded in your actual repo, with citations, instead of plausible guesses.
The surface is **read + Q&A only** (no write/approve tools), so approval stays a
human, CLI action.

## Related

- [docs/code-indexing.md](../code-indexing.md) — the code-aware indexing reference
- [docs/mcp.md](../mcp.md) — the full MCP tool catalogue and client config
- [Ask questions with GraphRAG](ask-questions-with-graphrag.md) — the same Q&A from the CLI
