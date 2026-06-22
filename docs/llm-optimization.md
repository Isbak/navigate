# LLM optimization strategy

This document describes how the catalog keeps LLM **cost** and **latency** low
without sacrificing classification **quality**. It is the strategy behind the
adaptive routing and prompt-caching features wired into `catalog classify`.

The guiding idea is simple: **spend tokens in proportion to difficulty.** Most
documents are ordinary prose that a small, fast model classifies just as well as
a large one; a minority — standards, regulations, engineering codes full of
equations, long and dense designs — genuinely need the strong model. The
pipeline already records what every call costs (`catalog cost-report`), so we can
both act on that signal and measure the result.

## Where the tokens go

`classify` is the dominant LLM cost in the pipeline. It makes **one call per
document chunk**, and each call carries:

| Part | Size | Varies per call? |
| --- | --- | --- |
| System prompt (persona + JSON schema + vocab + example + rules) | ~1k tokens | **No — constant** |
| Document metadata (filename, file_type, chunk note) | tiny | yes |
| Document text (a chunk, up to `max_input_chars`) | up to ~3k tokens | yes |
| Output (the JSON proposal) | variable | yes |

The other three call sites — `vision-extract`, `ask`, `merge` — are lower volume
but share the same provider, so they benefit from the same caching.

## The three pillars

### 1. Prompt caching (pure win, no behaviour change)

The classification instructions never change between documents, so they live in
the **system prompt** (`CLASSIFICATION_SYSTEM` in `catalog/semantic/prompts.py`)
and are sent byte-for-byte identically on every call. The Claude provider marks
that prefix with an Anthropic `cache_control: ephemeral` breakpoint, so on the
2nd+ call within the cache window the model:

- is billed at the **cache-read** rate (~10% of the input rate), and
- **skips re-processing** ~1k tokens of instructions, reducing latency.

Because a `classify` run makes many calls in quick succession (one per chunk, and
the cache persists across documents in a run), the cache hit rate is high. This
is a strict improvement: the exact same prompt is sent, so classification output
is unchanged. The savings are visible in the `cache_read` / `cache_write` columns
of the cost ledger (`catalog cost-report`), and `config/pricing.yml` already
prices those tokens.

Caching is on by default and can be turned off per provider with
`prompt_cache: false` in `config/llm.yml`.

### 2. Adaptive model routing (the headline)

Before a single token is spent, `catalog/semantic/routing.py` reads the
document's complexity with cheap, deterministic heuristics — length, symbol
density, equation markers (`\frac`, `\sqrt`, `^{...}`, …), and normative
"standards" language (`shall`, `Article`, `Clause`, `regulation`, `Annex`, …).
No LLM is involved, so profiling is free and explainable, in keeping with the
rest of the catalog's deterministic phases.

The resulting score selects the model:

- **Simple documents → fast model** (`claude-haiku-4-5` by default). Cheaper and
  faster.
- **Complex documents → deep model** (`claude-sonnet-4-5`). Documents with
  equations or normative language are *forced* to the deep model regardless of
  the blended score, because requirement/equation extraction hinges on them.

### 3. Confidence-based escalation (the safety net)

Routing to a cheaper model only saves money if quality holds. So after a
fast-model pass, if the document-type confidence comes back **below
`escalate_below_confidence`**, that one document is re-run on the deep model and
the deep result is kept. The strong model is therefore spent exactly where the
fast one was uncertain — turning "cheap by default, strong when it matters" into
a measurable policy rather than a gamble.

### Bonus: adaptive chunk budgeting

The fast path uses a smaller chunk cap (`fast_max_chunks`, default 6) than the
deep path (`max_chunks`, default 20), so the long tail of simple documents never
pays the full per-document chunk allowance, while complex documents keep their
full budget.

### Latency: concurrent classification

The optimizations above cut *cost*; concurrency cuts *wall-clock time*. Each
document is classified independently, and the LLM call is network-bound, so
`catalog classify --workers N` overlaps `N` documents' calls. All database
writes stay on one thread, so the result is byte-for-byte identical to a serial
run — only faster. The default lives in `config/performance.yml`:

```yaml
classify_workers: 4     # concurrent documents; 0 = one worker per CPU
```

This is *concurrency*, not a quota: every worker still issues real provider
calls, so set it no higher than your provider's rate limits (requests- and
tokens-per-minute) comfortably allow. Routing and prompt caching still apply per
call, and the cost ledger records the same per-chunk usage regardless of worker
count. `extract` and `discover-links` have their own `extract_workers` /
`link_workers` knobs in the same file.

## Configuration

All of this is configured in `config/llm.yml` and ships **enabled** by default:

```yaml
claude:
  model: claude-sonnet-4-5
  prompt_cache: true          # cache the constant system prompt

routing:
  enabled: true
  fast_model: claude-haiku-4-5
  deep_model: claude-sonnet-4-5
  complexity_threshold: 0.5       # score >= this -> deep model
  escalate_below_confidence: 0.6  # fast result below this -> re-run on deep
  fast_max_chunks: 6              # chunk budget for the fast path
```

`fast_model` and `deep_model` must belong to the active provider. To go back to
single-model classification, set `routing.enabled: false` (the single `model` is
then used for every document) — every other provider and the
no-config-file default already behave this way.

## How quality is preserved

- **Caching** sends an identical prompt, so output is unchanged by construction.
- **Routing** sends the genuinely hard documents (standards, equations, long
  dense files) to the strong model by deterministic rule, not by guess.
- **Escalation** re-runs any low-confidence fast-model result on the strong
  model, so a misrouted document is corrected rather than persisted cheaply.
- Everything remains a *proposal*: outputs still carry confidence, provenance,
  and `review_status = NEW`, so a human approves before anything is trusted.

## How to measure it

The cost ledger attributes every call to an operation, model, artifact, latency,
and USD cost, so the effect is directly observable:

```bash
catalog cost-report            # totals + breakdown by operation and by model
```

Expect to see: a share of `classify` tokens billed at the Haiku rate, cache-read
tokens replacing full-price input tokens, and a small number of escalated
documents billed twice (once fast, once deep). The breakdown **by model** shows
how much traffic the fast path absorbed; the cost-vs-quality view shows whether
the cheaper documents were also the ones the model was most confident about.

The same numbers are exposed over the REST API under the `cost` tag, so a
dashboard can chart spend without shelling out to the CLI: `GET /api/cost/summary`,
`/by-operation`, `/by-model`, `/per-document?top=N`, and `/vs-quality?top=N`.
These are a read-only projection of the cost ledger and make no LLM calls. See
[the REST API contract notes](navigate-api.md#cost--llm-usage--cost-tag).

## Design boundaries

- Routing decisions are **deterministic and free** (no extra LLM call to choose a
  model), consistent with the catalog's other rule-based phases.
- The router is **provider-agnostic**: it is handed already-built providers, so
  any backend offering a small and a large model can use it.
- The service path is **identical** with routing on or off — a disabled router
  simply always returns the one provider — so single-model setups and existing
  tests are unaffected.
