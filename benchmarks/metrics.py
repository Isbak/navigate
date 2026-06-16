"""Pure metric helpers for the benchmark suite.

Deliberately dependency-free and side-effect-free (mirroring the project's
scoring modules), so they are trivial to unit test and reuse across stages.

Two families:

* **quality** - set-based precision/recall/F1, label accuracy, and a pairwise
  clustering score for consolidation;
* **performance** - a wall-clock :class:`Timer` and a ``throughput`` helper.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

_WS_RE = re.compile(r"\s+")


def normalize(value: object) -> str:
    """Lowercase, trim, and collapse internal whitespace for fair comparison."""

    return _WS_RE.sub(" ", str(value).strip().lower())


def prf1(predicted: set[str], gold: set[str]) -> dict:
    """Precision / recall / F1 for two sets of (already-normalized) strings.

    Empty gold and empty prediction is treated as a perfect score (1.0): there
    was nothing to find and nothing was wrongly produced.
    """

    tp = len(predicted & gold)
    fp = len(predicted - gold)
    fn = len(gold - predicted)
    if not predicted and not gold:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 0, "fp": 0, "fn": 0}
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def normalized_prf1(predicted, gold) -> dict:
    """``prf1`` after normalizing every member of both iterables."""

    return prf1({normalize(p) for p in predicted}, {normalize(g) for g in gold})


def label_accuracy(pairs: list[tuple[object, object]]) -> float:
    """Fraction of ``(predicted, gold)`` pairs whose normalized labels match."""

    if not pairs:
        return 1.0
    correct = sum(1 for pred, gold in pairs if normalize(pred) == normalize(gold))
    return round(correct / len(pairs), 4)


def fraction(correct: int, total: int) -> float:
    """``correct / total`` with an empty-set convention of 1.0."""

    return 1.0 if total == 0 else round(correct / total, 4)


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 1.0


class Timer:
    """Context manager measuring wall-clock elapsed time in milliseconds.

    >>> with Timer() as t:
    ...     do_work()
    >>> t.ms        # elapsed milliseconds
    >>> t.seconds   # elapsed seconds
    """

    def __init__(self) -> None:
        self._start = 0.0
        self._end = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self._end = time.perf_counter()

    @property
    def seconds(self) -> float:
        return self._end - self._start

    @property
    def ms(self) -> float:
        return round(self.seconds * 1000.0, 3)


def throughput(n_items: int, seconds: float) -> float:
    """Items processed per second, guarding against a zero interval."""

    if seconds <= 0:
        return float(n_items) if n_items else 0.0
    return round(n_items / seconds, 2)


def performance(n_items: int, seconds: float) -> dict:
    """Standard performance block: total ms, per-item ms, items/sec."""

    return {
        "items": n_items,
        "total_ms": round(seconds * 1000.0, 3),
        "ms_per_item": round((seconds * 1000.0 / n_items), 3) if n_items else 0.0,
        "items_per_sec": throughput(n_items, seconds),
    }


@dataclass
class StageResult:
    """The outcome of one stage benchmark: quality + performance + gate status."""

    stage: str
    quality: dict = field(default_factory=dict)
    performance: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and not self.failures

    def gate(self, thresholds: dict) -> None:
        """Record which quality metrics fell below their threshold."""

        for metric, minimum in (thresholds or {}).items():
            if metric.startswith("_"):
                continue
            value = self.quality.get(metric)
            if value is None or value < minimum:
                self.failures.append(
                    f"{metric}={value!r} < required {minimum}"
                )

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "quality": self.quality,
            "performance": self.performance,
            "failures": self.failures,
            "error": self.error,
        }


__all__ = [
    "normalize",
    "prf1",
    "normalized_prf1",
    "label_accuracy",
    "fraction",
    "mean",
    "Timer",
    "throughput",
    "performance",
    "StageResult",
]
