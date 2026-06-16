"""Benchmark harness orchestrator.

Runs the five pipeline stages in dependency order over a single shared workspace
and aggregates their quality + performance metrics. The stages form a hard chain
(extract needs scan, classify needs extract, ...), so the whole chain always
executes; ``--stages`` only filters which results are reported and gated.

Usage::

    python -m benchmarks                       # all stages, stub provider, table
    python -m benchmarks --stages classify,ask
    python -m benchmarks --provider claude --format both --out exports/bench.json
    python -m benchmarks --check               # exit non-zero if a gate fails
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .corpus import load_corpus, materialize_docs, write_sources_yml
from .providers import StubAnswerProvider, StubClassifyProvider
from .report import build_report, render_console, write_json
from .stages import STAGE_ORDER, BenchContext
from .stages import (
    ask_bench,
    classify_bench,
    consolidate_bench,
    extract_bench,
    scan_bench,
)

_STAGE_RUNNERS = {
    "scan": scan_bench.run,
    "extract": extract_bench.run,
    "classify": classify_bench.run,
    "consolidate": consolidate_bench.run,
    "ask": ask_bench.run,
}

DEFAULT_THRESHOLDS = Path(__file__).resolve().parent / "thresholds.json"


def _load_thresholds(path: str | Path | None) -> dict:
    path = Path(path) if path else DEFAULT_THRESHOLDS
    return json.loads(path.read_text(encoding="utf-8"))


def _build_providers(provider_name: str, corpus, llm_config_path: str):
    """Return ``(classify_provider, answer_provider)`` for the chosen mode."""

    if provider_name == "stub":
        return StubClassifyProvider(corpus.classify_responses()), StubAnswerProvider()

    # Real provider mode: route both LLM stages through the configured backend.
    from catalog.semantic.config import load_llm_config
    from catalog.semantic.providers import build_provider

    config = load_llm_config(llm_config_path)
    config = dataclasses.replace(config, provider=provider_name)
    provider = build_provider(config)
    return provider, provider


def run_suite(
    *,
    stages: list[str] | None = None,
    provider: str = "stub",
    thresholds: dict | None = None,
    llm_config_path: str = "config/llm.yml",
    workdir: Path | None = None,
) -> list:
    """Run the benchmark chain and return the requested stages' results.

    The full chain always executes (stages depend on one another); ``stages``
    selects which results are returned and gated. ``thresholds`` (per-stage) are
    applied to populate each result's gate failures.
    """

    selected = list(stages) if stages else list(STAGE_ORDER)
    thresholds = thresholds if thresholds is not None else _load_thresholds(None)
    corpus = load_corpus()

    classify_provider, answer_provider = _build_providers(
        provider, corpus, llm_config_path
    )

    owns_tmp = workdir is None
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="navigate-bench-"))
    try:
        docs_dir = materialize_docs(tmp / "docs")
        sources_yml = write_sources_yml(tmp / "sources.yml", docs_dir)
        ctx = BenchContext(
            corpus=corpus,
            workdir=tmp,
            db_path=str(tmp / "catalog.sqlite"),
            cache_dir=str(tmp / "cache"),
            docs_dir=docs_dir,
            sources_yml=sources_yml,
            classify_provider=classify_provider,
            answer_provider=answer_provider,
            provider_name=provider,
        )

        # Stages depend only on upstream stages, so run from the start up to the
        # latest selected stage and no further. This keeps a subset like
        # ``--stages classify`` from triggering downstream LLM calls (ask) in
        # real-provider mode.
        last = max(STAGE_ORDER.index(s) for s in selected if s in STAGE_ORDER)

        all_results = {}
        for stage in STAGE_ORDER[: last + 1]:
            result = _STAGE_RUNNERS[stage](ctx)
            result.gate(thresholds.get(stage, {}))
            all_results[stage] = result
            # A broken upstream stage makes downstream metrics meaningless; stop
            # early so the failure is reported against the stage that caused it.
            if result.error is not None:
                break

        return [all_results[s] for s in selected if s in all_results]
    finally:
        if owns_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def _metadata(provider: str) -> dict:
    return {
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmarks", description="Run the navigate pipeline benchmark suite."
    )
    parser.add_argument(
        "--stages",
        default="all",
        help=f"comma-separated subset of {','.join(STAGE_ORDER)} (default: all)",
    )
    parser.add_argument(
        "--provider",
        default="stub",
        help="stub (deterministic, default) or a real provider: claude/openai/ollama",
    )
    parser.add_argument("--out", default=None, help="write the JSON report to this path")
    parser.add_argument(
        "--format", choices=["table", "json", "both"], default="table",
        help="console output format (a file is always written when --out is set)",
    )
    parser.add_argument("--thresholds", default=None, help="path to a thresholds JSON")
    parser.add_argument("--llm-config", default="config/llm.yml")
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if any reported stage fails its quality gate",
    )
    args = parser.parse_args(argv)

    stages = None if args.stages in ("all", "") else [
        s.strip() for s in args.stages.split(",") if s.strip()
    ]
    thresholds = _load_thresholds(args.thresholds)

    results = run_suite(
        stages=stages,
        provider=args.provider,
        thresholds=thresholds,
        llm_config_path=args.llm_config,
    )
    report = build_report(results, _metadata(args.provider))

    if args.format in ("table", "both"):
        print(render_console(report))
    if args.format in ("json", "both"):
        print(json.dumps(report, indent=2))
    if args.out:
        path = write_json(report, args.out)
        print(f"\nWrote {path}")

    if args.check and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
