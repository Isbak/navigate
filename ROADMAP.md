# Roadmap

Navigate is a local-first knowledge platform. This roadmap captures what's
planned next, grouped by the five tracking labels used on issues and PRs:

| Label | Focus |
| --- | --- |
| `security` | Authentication, secrets, network exposure, data protection |
| `ux` | CLI/API ergonomics, output, docs, onboarding |
| `graph` | Knowledge graph, exploration, RDF/SPARQL |
| `governance` | Review workflows, ownership, freshness, audit |
| `performance` | Throughput, scaling, caching, type/quality gates |

Items here are intentionally coarse; each becomes one or more GitHub issues with
the matching label. See `.github/labels.yml` for the canonical label set.

## Recently shipped

- Modular CLI: commands split into `src/catalog/commands/*` with uniform
  `register()` / `set_defaults(func=...)` dispatch. (`ux`)
- `catalog doctor` — one-shot health check for config, database, cache, LLM
  keys, Fuseki and permissions. (`ux`)
- Insecure-bind warning when the API binds to `0.0.0.0` without an API key,
  surfaced at server start and by `doctor`. (`security`)
- Per-module mypy gate: `catalog.commands.*` and the API server/config modules
  are type-checked strictly in CI; the rest stays advisory. (`performance`)
- Smoke tests for API startup and Docker invariants. (`performance`)

## Near-term

- **Harden API auth defaults** — make `require_api_key` easier to enable, warn
  louder on wildcard binds, document a production checklist. (`security`)
- **Promote more modules to the mypy strict gate** — burn down the advisory
  baseline module by module (next: `links`, `knowledge`, `rdf`). (`performance`)
- **`doctor` deepening** — optional checks for disk space, schema version drift,
  and a `--fix` for safe remediations (create cache dir, init DB). (`ux`)
- **Graph exploration polish** — richer `graph` output and saved queries.
  (`graph`)
- **Governance review ergonomics** — bulk review UX and clearer stale/owner
  reporting. (`governance`)

## Later

- Optional end-to-end Docker test in CI behind a dedicated job. (`performance`)
- SPARQL/RDF round-trip validation against a live Fuseki in CI. (`graph`)
- Pluggable auth backends for the API (token store, OIDC). (`security`)
