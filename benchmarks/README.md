# Pipeline benchmark suite

Measures both **quality/accuracy** and **performance/throughput** for the five
core pipeline stages — `scan`, `extract`, `classify`, `consolidate`, `ask` —
against a small, in-repo gold corpus.

The suite is **deterministic and CI-runnable by default**: the LLM-driven stages
(`classify`, `ask`) use canned `StubProvider` responses derived from the gold
corpus, so no API key or network is required. Pass `--provider` to score a real
model instead.

## Running

```bash
# all stages, deterministic stub provider, console table
python -m benchmarks
catalog benchmark

# a subset, JSON + table, written to a file
python -m benchmarks --stages classify,ask --format both --out exports/bench.json

# fail (non-zero exit) if any stage misses its quality gate
python -m benchmarks --check

# score a real model (needs an API key configured in config/llm.yml)
python -m benchmarks --provider claude --stages classify
```

The stages form a hard chain (extract needs scan, classify needs extract, …),
so the whole chain always executes; `--stages` only filters which results are
reported and gated.

## What each stage measures

| Stage         | Quality metrics                                                        | Performance      |
|---------------|------------------------------------------------------------------------|------------------|
| `scan`        | delta-detection accuracy (RAW/CHANGED/UNCHANGED/DELETED), duplicates    | files/sec        |
| `extract`     | text-marker recall, link precision/recall/F1                            | docs/sec         |
| `classify`    | document-type accuracy, F1 for domains/capabilities/entities/decisions/risks/relationships | docs/sec |
| `consolidate` | object F1, cross-document merge accuracy, relationship recall           | mentions/sec     |
| `ask`         | retrieval recall, groundedness accuracy, citation rate                  | questions/sec    |

In deterministic mode the LLM stages return the gold response, so `classify`/`ask`
quality validates the parser/persistence/retrieval paths (a stable ~1.0
baseline). With `--provider` the same gold becomes the comparison target for a
real model's accuracy.

## Layout

```
benchmarks/
  metrics.py      pure metric helpers (P/R/F1, accuracy, Timer, StageResult)
  providers.py    deterministic StubClassifyProvider / StubAnswerProvider
  corpus.py       loads the gold corpus; materialize/approve helpers
  stages/         one runner per stage (scan/extract/classify/consolidate/ask)
  report.py       JSON + rich console table
  runner.py       orchestrator (run_suite + CLI main)
  thresholds.json quality gates asserted in stub mode
  corpus/
    docs/         small fixture documents (.md / .txt)
    gold/         expected outputs per stage
```

## CI gate

`tests/test_benchmarks.py` runs the suite in stub mode and asserts every stage
meets its thresholds, inside the existing `pytest -q` job. Performance numbers
are recorded but never gated (CI-runner variance).
