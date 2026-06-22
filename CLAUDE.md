# Working in this repository

Navigate is a local-first knowledge platform. The Python package is named
`catalog` (CLI: `catalog`, with `navigate` as an alias). Source lives under
`src/catalog/`; the main CLI is argparse-based in `src/catalog/cli.py`, with
sub-CLIs for `graph`, `governance`, `graphrag`, `compliance`, and `mcp`.

## Keeping docs in sync

The README is a scannable overview that links out to task-oriented guides under
`docs/how-to/` and reference docs under `docs/`. Commands and config keys live in
code, so docs do not update themselves — keep them in sync **in the same change**
that touches behavior.

When you add, rename, or remove a **CLI command/flag** or a **`config/*.yml`
key**, update the matching how-to guide **and** the README use-case/quickstart
section in the same PR. Prefer linking to `catalog <cmd> --help` over re-listing
every flag, and keep **one source of truth per topic** — do not copy the same
flag table into multiple docs.

Command area → doc to update:

| Command area | Doc |
| --- | --- |
| `init-db`, `scan`, `watch`, `stats`, `show-duplicates` | `docs/how-to/catalog-your-files.md` |
| `extract`, `discover-links`, `link-stats`, `show-links`, `show-stale-links`, `export-links-csv` | `docs/how-to/discover-and-classify-links.md` |
| `classify`, `classification-stats`, `consolidate`, `clean-source`, `knowledge-stats`, review/approve commands | `docs/how-to/build-a-knowledge-graph.md` |
| `graph *` | `docs/how-to/explore-the-knowledge-graph.md` |
| `ask`, `explain`, `compare`, `impact`, `path-reason` | `docs/how-to/ask-questions-with-graphrag.md` |
| `governance *` | `docs/how-to/govern-your-knowledge.md` |
| `rdf-export`, `rdf-validate`, `rdf-stats`, `fuseki-load`, `fuseki-clear` | `docs/how-to/publish-to-rdf-and-sparql.md` |
| `mcp`, code-aware indexing (`index_code`) | `docs/how-to/ground-an-ai-agent-in-your-code.md` (+ `docs/code-indexing.md`, `docs/mcp.md`) |
| `compliance *` | `docs/compliance.md` |
| `api` / REST endpoints | `docs/navigate-api.md` |
| `cost-report`, LLM routing/caching | `docs/llm-optimization.md` |

If a change spans several areas (e.g. a new pipeline stage), also update the
how-to hub `docs/how-to/README.md` and the README's "What can I do with it?"
table.

## Development

```bash
pytest                    # tests (quiet; config in pyproject.toml)
ruff check .              # lint (enforced in CI)
ruff format .             # format (not gated yet)
mypy                      # advisory in CI
```
