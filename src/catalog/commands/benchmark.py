"""The ``benchmark`` command: run the scan/extract/classify/consolidate/ask
benchmark suite from the top-level ``benchmarks`` package."""

from __future__ import annotations

import argparse


def _cmd_benchmark(args: argparse.Namespace) -> int:
    """Dispatch to the benchmark harness (kept in the top-level ``benchmarks`` package)."""

    try:
        from benchmarks.runner import main as run_benchmarks
    except ImportError as exc:
        print(
            "Error: the benchmark suite is unavailable "
            f"(could not import 'benchmarks': {exc}).\n"
            "Run from the repository root or reinstall with 'pip install -e .'."
        )
        return 1

    argv = ["--stages", args.stages, "--provider", args.provider, "--format", args.format]
    argv += ["--llm-config", args.llm_config]
    if args.out:
        argv += ["--out", args.out]
    if args.thresholds:
        argv += ["--thresholds", args.thresholds]
    if args.check:
        argv.append("--check")
    result: int = run_benchmarks(argv)
    return result


def register(sub: argparse._SubParsersAction) -> None:
    bench = sub.add_parser(
        "benchmark", help="run the scan/extract/classify/consolidate/ask benchmark suite"
    )
    bench.add_argument(
        "--stages",
        default="all",
        help="comma-separated subset of scan,extract,classify,consolidate,ask",
    )
    bench.add_argument(
        "--provider",
        default="stub",
        help="stub (deterministic) or a real provider: claude/openai/ollama",
    )
    bench.add_argument("--out", default=None, help="write the JSON report to this path")
    bench.add_argument("--format", choices=["table", "json", "both"], default="table")
    bench.add_argument("--thresholds", default=None, help="path to a thresholds JSON")
    bench.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any reported stage fails its quality gate",
    )
    bench.set_defaults(func=_cmd_benchmark)


__all__ = ["register"]
