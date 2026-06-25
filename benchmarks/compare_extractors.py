"""Standalone extractor comparison harness.

Runs the current, MarkItDown, and Docling extractors against the binary
fixture corpus and reports quality and performance metrics side-by-side.

Usage:
    python -m benchmarks.compare_extractors [--backends current,markitdown,docling]
    python -m benchmarks.compare_extractors --out exports/extractor_comparison.json

The harness is intentionally standalone (not wired into STAGE_ORDER) to avoid
disrupting the existing CI gate, which only covers the text/Markdown corpus.

Backends:
    current     The existing per-format extractors (PyMuPDF, python-docx, etc.)
    markitdown  MarkItDown adapter (mode: enhanced). Requires markitdown extra.
    docling     IBM Docling adapter (mode: docling). Requires docling extra.
"""

from __future__ import annotations

import argparse
import json
import tracemalloc
from pathlib import Path

from catalog.extraction import extract_links_from_text
from catalog.extractors import get_extractor
from catalog.extractors.config import MODE_DOCLING, MODE_ENHANCED, MODE_FAST

from .metrics import (
    Timer,
    fraction,
    mean,
    normalized_prf1,
    reading_order_score,
    table_recall,
    verbatim_quote_rate,
)

FIXTURE_DIR = Path(__file__).parent / "corpus" / "binary_fixtures"
GOLD_PATH = Path(__file__).parent / "corpus" / "gold" / "extract_binary.json"

_BACKEND_MODE = {
    "current": MODE_FAST,
    "markitdown": MODE_ENHANCED,
    "docling": MODE_DOCLING,
}


def _extract(path: Path, backend: str) -> tuple[str, float, float]:
    """Return ``(text, elapsed_ms, peak_mb)``."""
    mode = _BACKEND_MODE[backend]

    tracemalloc.start()
    with Timer() as t:
        extractor = get_extractor(path, mode)
        text = "" if extractor is None else extractor.extract_text(path)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return text, t.ms, round(peak / 1_048_576, 2)


def _score_fixture(
    text: str, spec: dict, links_raw: list[dict]
) -> dict[str, float]:
    text_markers = spec.get("text_markers", [])
    gold_links = spec.get("links", [])
    t_markers = spec.get("table_markers", [])
    ro_pairs = spec.get("reading_order_check", [])
    vq_samples = spec.get("verbatim_quote_samples", [])

    found = sum(1 for m in text_markers if m.lower() in text.lower())
    text_rec = fraction(found, len(text_markers)) if text_markers else 1.0

    predicted_urls = {lk["raw_url"] for lk in links_raw}
    link_scores = normalized_prf1(predicted_urls, set(gold_links))

    return {
        "text_recall": text_rec,
        "link_f1": link_scores["f1"],
        "link_precision": link_scores["precision"],
        "link_recall": link_scores["recall"],
        "table_recall": table_recall(text, t_markers),
        "reading_order_score": reading_order_score(text, ro_pairs),
        "verbatim_quote_rate": verbatim_quote_rate(text, vq_samples),
    }


def run_backend(backend: str, gold: dict) -> dict:
    """Run one backend over all fixtures and return its result dict."""
    results: dict[str, dict] = {}
    total_ms: list[float] = []
    peak_mbs: list[float] = []
    cold_start_ms: float | None = None

    for filename, spec in gold.items():
        if filename.startswith("_"):
            continue
        path = FIXTURE_DIR / filename
        if not path.exists():
            results[filename] = {"error": "fixture missing"}
            continue

        # Cold start measurement for Docling (first document load)
        if backend == "docling" and cold_start_ms is None:
            try:
                from catalog.extractors.docling_extractor import _CONVERTER
                if _CONVERTER is None:
                    cold_start_ms = 0.0
                else:
                    with Timer() as cs:
                        _CONVERTER.convert(str(path))
                    cold_start_ms = cs.ms
            except Exception:
                cold_start_ms = 0.0

        try:
            text, elapsed_ms, peak_mb = _extract(path, backend)
        except ImportError as exc:
            results[filename] = {"error": f"ImportError: {exc}"}
            continue
        except Exception as exc:
            results[filename] = {"error": f"{type(exc).__name__}: {exc}"}
            continue

        links_raw = extract_links_from_text(text)
        scores = _score_fixture(text, spec, links_raw)
        scores["ms_per_doc"] = elapsed_ms
        scores["peak_mb"] = peak_mb
        results[filename] = scores
        total_ms.append(elapsed_ms)
        peak_mbs.append(peak_mb)

    aggregate: dict[str, float] = {}
    metric_keys = [
        "text_recall", "link_f1", "table_recall",
        "reading_order_score", "verbatim_quote_rate",
    ]
    for k in metric_keys:
        vals = [r[k] for r in results.values() if isinstance(r.get(k), float)]
        aggregate[k] = mean(vals) if vals else 0.0
    aggregate["avg_ms_per_doc"] = mean(total_ms) if total_ms else 0.0
    aggregate["avg_peak_mb"] = mean(peak_mbs) if peak_mbs else 0.0
    if cold_start_ms is not None:
        aggregate["cold_start_ms"] = cold_start_ms

    return {"per_fixture": results, "aggregate": aggregate}


def print_table(all_results: dict[str, dict]) -> None:
    metrics = [
        "text_recall", "link_f1", "table_recall",
        "reading_order_score", "verbatim_quote_rate",
        "avg_ms_per_doc", "avg_peak_mb",
    ]
    backends = list(all_results.keys())
    col_w = 16

    header = f"{'Metric':<24}" + "".join(f"{b:<{col_w}}" for b in backends)
    print(header)
    print("-" * (24 + col_w * len(backends)))

    for metric in metrics:
        row = f"{metric:<24}"
        for backend in backends:
            agg = all_results[backend].get("aggregate", {})
            val = agg.get(metric)
            row += f"{str(round(val, 3)) if val is not None else 'n/a':<{col_w}}"
        print(row)

    cold_row = f"{'cold_start_ms':<24}"
    any_cold = False
    for backend in backends:
        agg = all_results[backend].get("aggregate", {})
        val = agg.get("cold_start_ms")
        if val is not None:
            cold_row += f"{round(val, 1):<{col_w}}"
            any_cold = True
        else:
            cold_row += f"{'n/a':<{col_w}}"
    if any_cold:
        print(cold_row)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backends",
        default="current",
        help="Comma-separated list: current,markitdown,docling",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=FIXTURE_DIR,
        help="Directory containing binary fixture files",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=GOLD_PATH,
        help="Path to extract_binary.json gold spec",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write full results JSON to this file",
    )
    args = parser.parse_args(argv)

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    backends = [b.strip() for b in args.backends.split(",")]

    all_results: dict[str, dict] = {}
    for backend in backends:
        if backend not in _BACKEND_MODE:
            print(f"Unknown backend {backend!r}. Choose from: {', '.join(_BACKEND_MODE)}")
            continue
        print(f"\nRunning backend: {backend}")
        all_results[backend] = run_backend(backend, gold)

    print("\n=== Summary ===\n")
    print_table(all_results)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
        print(f"\nFull results written to {args.out}")


if __name__ == "__main__":
    main()
