"""Per-stage benchmark runners.

Each module exposes ``run(ctx) -> StageResult``. A stage both *advances the
shared pipeline* (so the next stage has real upstream output to measure) and
*computes its own quality + performance metrics* against the gold corpus. The
runner invokes them in dependency order over a single shared workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from catalog.semantic.providers.base import BaseLLMProvider

from ..corpus import Corpus

STAGE_ORDER = ("scan", "extract", "classify", "consolidate", "ask")


@dataclass
class BenchContext:
    """Shared state threaded through the pipeline stages."""

    corpus: Corpus
    workdir: Path
    db_path: str
    cache_dir: str
    docs_dir: Path
    sources_yml: Path
    classify_provider: BaseLLMProvider
    answer_provider: BaseLLMProvider
    provider_name: str = "stub"


__all__ = ["BenchContext", "STAGE_ORDER"]
