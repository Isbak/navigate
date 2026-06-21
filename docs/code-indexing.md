# Code-aware indexing

The catalog indexes source code as a first-class artifact type, alongside
documents. A repository can be pointed at the scanner and produce the same
review → approve knowledge graph (entities, relationships, summaries, risks)
that documents produce — but read *as code* rather than as flat text.

This is on by default. Set `index_code: false` in `config/sources.yml` to index
documents only.

## What "code-aware" means

Source code differs from prose in two ways the pipeline now respects:

1. **Structure is meaningful.** A function or class is a unit; splitting one in
   half loses meaning. Code is parsed with
   [tree-sitter](https://tree-sitter.github.io/) and chunked **only at top-level
   construct boundaries**, so every definition reaches the model whole. The file
   is preserved verbatim — chunks are contiguous spans of the original lines.
2. **The interesting entities are different.** Documents yield capabilities,
   decisions, and risks; code additionally yields modules, classes, functions,
   the libraries it imports, and the services/APIs it talks to.

## Pipeline

The code path reuses the document pipeline end to end
(`scanner → extraction → classify`); only the chunking and the prompt change,
and a deterministic syntax outline is folded in.

```
scan      ─ scanner picks up source files (SUPPORTED_EXTENSIONS ∪ CODE_EXTENSIONS)
extract   ─ source is cached verbatim; language is tagged in metadata.json and a
            syntax outline is written to cache/<id>/code_structure.json
classify  ─ code-aware chunking (chunk_code) + code prompt (code_prompts) →
            merged with the deterministic structure → candidate_* tables
```

### Two layers of knowledge

| Layer | Source | Confidence | Examples |
| --- | --- | --- | --- |
| **Structural** (deterministic) | tree-sitter syntax tree | ~0.99 | `Module`, `Class`, `Function`, `Library` entities; `defines`, `imports` relationships |
| **Semantic** (LLM) | code-specific prompt | model-scored | module purpose, domains, services/APIs called, security/coupling risks, design decisions |

The structural layer is reliable even with **no LLM** and even if the model is
wrong; the semantic layer adds the understanding the syntax tree cannot. The two
are merged with the existing
`merge_classification_results` (highest-confidence instance per key wins), so
the model's document-level summary stays while the precise structural entities
are added.

These map onto the **same** `ClassificationResult` and `candidate_*` tables as
documents, so consolidation, the knowledge graph, and RDF export need no
changes. The new controlled-vocabulary values live in
`catalog/semantic/models.py` (`DOCUMENT_TYPES += "Source Code"`; `ENTITY_TYPES`
gains `Module/Class/Function/Library/Service/Interface/API`;
`RELATIONSHIP_PREDICATES` gains `imports/calls/extends/exposes/defines`).

## Languages and grammars

Each language uses a tree-sitter grammar wheel from the `code` extra
(`pip install -e '.[code]'`, included in `.[dev]`). Supported languages:
Python, JavaScript, TypeScript/TSX, Go, Rust, Java, C, C++, C#, Ruby, PHP, Bash.

Grammars are loaded lazily and **fault-tolerantly**: if tree-sitter or a
particular grammar is not installed, the file is still ingested and classified —
it just falls back to character-based chunking and an empty syntax outline. The
catalog never crashes because a grammar is missing.

## Configuration

```yaml
# config/sources.yml
index_code: true   # default; false indexes documents only
```

When code indexing is on, a set of vendor/build excludes
(`node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `target`,
`vendor`, `*.min.js`, …) is added automatically on top of your `exclude`
patterns, so a scan does not drown in dependencies.

## Implementation map

| Concern | Location |
| --- | --- |
| Extension → language | `catalog/code/languages.py` |
| Lazy, fault-tolerant grammar loading | `catalog/code/parser.py` |
| Syntax outline (imports/classes/functions) | `catalog/code/structure.py` |
| Boundary-aware chunking | `catalog/code/chunking.py` |
| Outline → `ClassificationResult` | `catalog/code/to_result.py` |
| Code classification prompt/schema | `catalog/semantic/code_prompts.py` |
| Code branch in the classify service | `catalog/semantic/service.py` |
