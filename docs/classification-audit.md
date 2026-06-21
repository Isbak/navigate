# Classification & knowledge-discovery audit

This document records how the catalog turns documents into a knowledge graph,
where that pipeline loses precision (noise, near-duplicate nodes, inconsistent
classification), and the precision fixes applied in response. It is a companion
to the module docstrings in `catalog/semantic/` and `catalog/knowledge/`.

## The two LLM-fed stages

```
filesystem -> scanner -> SQLite -> document cache -> link discovery
                                          |
                                          +--> semantic classification  (catalog/semantic)
                                                    |  candidate_* tables
                                                    +--> knowledge consolidation (catalog/knowledge)
                                                              knowledge_objects / _relationships
```

### 1. Semantic classification (`catalog/semantic/`)
- `service._classify_one` reads `cache/<id>/extracted.txt`, splits it into
  chunks (`prompts.chunk_text`), and classifies **each chunk independently**
  against the constant system prompt (`prompts.CLASSIFICATION_SYSTEM`).
- `parser.merge_classification_results` concatenates the per-chunk lists and
  dedups by a lowercased natural key, keeping the highest-confidence instance.
  `document_type`/summaries come from the single most-confident chunk.
- `routing.ProviderRouter` does a cheap, deterministic complexity read (length,
  symbol density, equation/standards markers — no LLM) to pick a fast vs deep
  model and a chunk budget; an uncertain fast result escalates to the deep model.
- `parser` clamps every confidence to `[0,1]`, normalizes `document_type` to the
  vocabulary (else `Other`), keeps only known predicates, and defaults an unknown
  `entity_type` to `Concept`. The output lands in the `candidate_*` tables.

### 2. Knowledge consolidation (`catalog/knowledge/`)
- `repository.gather_mentions` flattens `candidate_capabilities` (→Capability),
  `candidate_decisions` (→Decision), `candidate_risks` (→Risk),
  `candidate_entities` (type in a column), and requirements/equations into a flat
  list of `RawMention(object_type, name, …)`.
- `resolution.cluster_mentions` buckets by `(object_type, normalize_name(name))`
  (exact), then fuzzy-merges buckets **of the same type only** via union-find
  when `similarity() ≥ auto_merge_threshold (0.88)`; an optional LLM judge
  resolves the `review_threshold..auto_merge` band. `similarity()` blends a
  token-set jaccard, a character-trigram Dice score, and a containment boost.
- `service.consolidate` assigns each cluster a stable id (`<type>_<slug>`), builds
  a name→id index, resolves each candidate relationship's free-text
  `subject`/`object` back to object ids (exact, else global fuzzy ≥0.88; drops the
  rest), scores every object (`scoring.score_object`), and attaches evidence.
- Tables are **regenerable**: `db.init_db` drops & recreates any table whose
  columns no longer match the expected set, then `classify`/`consolidate`
  repopulate it. Adding a column is therefore a safe, idiomatic change.

## Findings — why the graph was noisy / had many similar nodes

1. **No confidence floor.** `ResolutionConfig.min_mention_confidence` defaulted to
   `0.0` and the CLI never overrode it, so every weak, one-off, low-confidence
   proposal became its own knowledge object. The largest pure-noise source.
2. **Decisions & risks never consolidated.** Their object `name` was the full
   sentence (`decision_text` / `risk_description`), which is unique per document,
   so the same decision worded two ways across five documents produced ~five
   separate Decision nodes with long slug ids. The dominant source of
   "many similar nodes".
3. **The containment boost over-merged.** Any token-subset was lifted to ≥0.88, so
   a lone generic term ("Governance", "Data") auto-merged into a specific name
   ("Release Governance", "Data Platform") — false merges that hurt accuracy.
4. **Cross-type fragmentation was invisible.** Clustering only merges within one
   `object_type`, so the same thing tagged `Concept` in one document and
   `Capability` in another became two nodes, with nothing surfacing them as the
   same thing.
5. **Classification isolation drifts surface forms.** No entity-vocabulary
   feedback and per-chunk independence let the same concept appear under many
   names/types, pushing all disambiguation onto fuzzy matching.

## Fixes applied

| # | Fix | Where |
| - | --- | ----- |
| 1 | `min_mention_confidence` default `0.0 → 0.3`; `consolidate --min-confidence` wired through to `ResolutionConfig` | `knowledge/resolution.py`, `cli.py` |
| 2 | Decisions/risks carry a short `title`; consolidation clusters on it (full text kept as evidence). New `title` column on `candidate_decisions`/`candidate_risks` | `semantic/{prompts,models,parser,repository}.py`, `db.py`, `knowledge/repository.py` |
| 3 | Containment boost only applies when the smaller token set has ≥2 significant tokens | `knowledge/resolution.py` |
| 4 | `cross_type_duplicate_pairs` surfaces same-name/different-type objects in `review-candidates` (never auto-merged) | `knowledge/resolution.py`, `knowledge/analytics.py`, `cli.py` |
| 5 | Prompt instructs the model to pick the single most specific `entity_type` and use one consistent name per thing | `semantic/prompts.py` |

After upgrading, re-run `catalog classify --force` (to populate decision/risk
titles and apply the prompt changes) then `catalog consolidate --force`.

## Backlog — bigger bets, not yet built

- **Entity-vocabulary feedback into classification.** Feed the top existing
  object names into the classification prompt so new documents reuse canonical
  names instead of inventing new surface forms — the largest remaining lever for
  "many similar nodes", but a meaningful change to the prompt/service contract.
- **Relationship recall.** Many edges are dropped because free-text endpoints do
  not resolve to an object (`relationships_unresolved`). Resolve endpoints against
  the per-document candidate entities first, and consider a lower
  relationship-resolution threshold gated behind review.
- **Alias / embedding similarity.** Acronym and synonym matches (e.g. ADO ↔ Azure
  DevOps) are out of reach for the current lexical blend; an alias table or
  embedding similarity would close that gap.
