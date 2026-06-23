from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    path: str
    source_system: str = "local_laptop"


@dataclass(frozen=True)
class CatalogConfig:
    sources: list[Source]
    exclude: list[str]
    # When True the scanner also ingests source-code files (code-aware indexing).
    index_code: bool = True


@dataclass(frozen=True)
class PerformanceConfig:
    """Worker-pool sizes for the parallelizable pipeline stages.

    ``0`` means "auto" and resolves to ``os.cpu_count()`` at the call site (see
    :func:`catalog.config.resolve_workers`). ``classify`` defaults to a small,
    fixed pool because each worker issues blocking LLM calls and a large pool
    would hammer provider rate limits.
    """

    extract_workers: int = 0
    link_workers: int = 0
    classify_workers: int = 4
