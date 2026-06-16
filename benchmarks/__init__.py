"""Benchmark suite for the navigate pipeline.

Measures both *quality/accuracy* and *performance/throughput* for the five core
pipeline stages - scan, extract, classify, consolidate, ask - against a small,
in-repo gold corpus.

The suite is deterministic and CI-runnable by default: the LLM-driven stages
(classify, ask) use canned ``StubProvider`` responses derived from the gold
corpus, so no API key or network is required. Pass ``--provider`` to score a
real model instead.

Entry points:

* ``python -m benchmarks`` / ``benchmarks.runner.main`` - standalone harness.
* ``catalog benchmark`` - the same harness behind the CLI.
* ``tests/test_benchmarks.py`` - a thin pytest gate asserting metric thresholds.
"""

from __future__ import annotations

__all__ = ["runner", "metrics", "corpus", "report"]
