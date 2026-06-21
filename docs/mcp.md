# MCP grounding server

`catalog mcp` exposes the catalog's **approved knowledge graph** and the
**GraphRAG assistant** as [Model Context Protocol](https://modelcontextprotocol.io)
tools, so an external agent (Claude Code, Claude Desktop, any MCP client) can
ground its reasoning in cited, confidence-scored knowledge instead of
free-associating.

It is a thin adapter over the existing services: every tool delegates to the same
helpers the CLI (`catalog graph …`, `catalog ask`) and the REST API already use —
no new business logic, no new SQL. The server speaks **stdio**, the transport an
MCP client launches as a subprocess.

## Install

The MCP runtime is an optional extra (the base install is unchanged):

```bash
pip install -e '.[mcp]'
```

`catalog.mcp.server` imports `mcp` lazily, so the rest of the catalog never
requires it. If you run `catalog mcp` without the extra installed, it exits with
an actionable `pip install '.[mcp]'` message.

## Run

```bash
catalog mcp                                   # uses data/catalog.sqlite
catalog mcp --db data/catalog.sqlite          # explicit database
catalog mcp --no-graphrag                      # graph-only, fully offline (no ask)
```

The database must already be populated and consolidated (the tools read the
**approved** graph). A typical pipeline first:

```bash
catalog scan && catalog extract && catalog classify && catalog consolidate
# approve the objects/relationships you trust, then:
catalog mcp
```

`--db` and `--llm-config` are the global `catalog` flags; `--queries-dir`
defaults to `queries`.

## Tools

| Tool | Needs LLM? | What it returns |
|------|:---------:|-----------------|
| `search_knowledge(term)` | no | objects whose label/description match `term` (id, label, type, description) |
| `get_object(object_id)` | no | one object's type, description, confidence, evidence count (accepts an id **or** a name) |
| `neighbors(object_id)` | no | directly connected objects grouped by relationship predicate |
| `impact(object_id)` | no | what a change may affect, neighbours grouped by object type |
| `find_path(source, target)` | no | shortest relationship path between two objects |
| `evidence_for(object_id)` | no | supporting evidence quotes (artifact, quote, confidence) |
| `ask(question, depth=2)` | yes | a graph-first, cited answer with a confidence band |

The graph-first tools are deterministic and run fully offline against the
in-memory rdflib projection built from SQLite. `ask` is the one tool that calls an
external LLM; it uses the provider in `config/llm.yml` (`ANTHROPIC_API_KEY` /
`OPENAI_API_KEY`, or a local Ollama model). When GraphRAG is disabled
(`--no-graphrag`) or no provider/key is configured, `ask` returns
`{"available": false, …}` instead of raising, so a client degrades gracefully to
the graph-first tools. `ask` token usage is priced and recorded like the CLI/API
paths, so it shows up in `catalog cost-report`.

`get_object` / `neighbors` / `impact` / `find_path` / `evidence_for` accept either
a stable object id (`capability_release_governance`) or a human name
(`"Release Governance"`); ambiguous or unknown names come back with
`found: false` and a `candidates` list.

## Connecting an MCP client

Add the server to your MCP client's config. For Claude Code:

```bash
claude mcp add navigate-knowledge -- catalog mcp --db /abs/path/to/data/catalog.sqlite
```

Or, equivalently, a raw client config block:

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

Use an absolute `--db` path so the tools resolve the same database regardless of
the client's working directory. Set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in
the client's environment if you want the `ask` tool enabled.

## Scope

Read + Q&A only — there are no write/approve tools, which keeps the agent surface
safe (approval stays a human, CLI/API action). Only **stdio** transport is
provided today; an HTTP/SSE transport can be added later for a remote client.
