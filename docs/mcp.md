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

### Write tools (opt-in, policy-gated)

By default the server is **read + Q&A only**. Pass `--enable-agent-review` to also
expose three write tools that let an agent take the high-confidence, low-risk
review decisions off a human's plate — without giving up governance:

```bash
catalog mcp --enable-agent-review            # also reads config/governance.yml
```

| Tool | What it does |
|------|--------------|
| `approve_object(object_id, note="")` | Approve one `PROPOSED` object **iff** it passes the agent-review policy |
| `approve_relationship(relationship_id, note="")` | Approve one `PROPOSED` relationship **iff** it passes the policy |
| `flag_object(object_id, note="")` | Escalate an uncertain object to the human review queue (`NEEDS_ATTENTION`) instead of approving — the safe complement to approval |

These stay safe by construction:

- **The agent never sets its own thresholds or identity.** They come from the
  `agent_review` block in `config/governance.yml` (confidence window, evidence
  requirement, object-type/predicate allowlists), not from the tool arguments.
  The tools are also inert unless **both** `--enable-agent-review` (server flag)
  and `agent_review.enabled: true` (config) are set.
- **Every decision is attributable.** Approvals reuse the normal `APPROVED` state
  but are tagged `agent:<agent_name>` in the reviewer column, so they are
  filterable in `catalog governance history` and the change-log feed.
- **Every decision is reversible.** A human undoes a single one with
  `catalog governance revert <id>` or a whole batch with
  `catalog governance revert-agent --agent <name>` (see
  [govern-your-knowledge](how-to/govern-your-knowledge.md)).

When a target is missing, already decided, or outside the policy, the tool
returns `{"approved": false, "reason": …}` rather than raising — the same
graceful-decline shape as `ask`. Object/relationship ids come from the review
queue (`catalog governance review-queue`, the REST API, or a human).

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

Read + Q&A by default; opt-in, **policy-gated** writes via `--enable-agent-review`
(see [Write tools](#write-tools-opt-in-policy-gated)). The write surface is
bounded by config, attributable (`agent:<name>`), and reversible, so a human stays
in control even when an agent does the routine approving. Only **stdio** transport
is provided today; an HTTP/SSE transport can be added later for a remote client.
