from __future__ import annotations

import os
from pathlib import Path

import yaml

from .models import CatalogConfig, PerformanceConfig, Source

DEFAULT_CONFIG_PATH = Path("config/sources.yml")
DEFAULT_PERFORMANCE_CONFIG_PATH = Path("config/performance.yml")

# Directories/files that are source code but not *our* code: dependencies, build
# output, and caches. Applied automatically when code indexing is enabled so a
# repository scan does not drown in vendored libraries.
DEFAULT_CODE_EXCLUDES = [
    "**/.git/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/dist/**",
    "**/build/**",
    "**/target/**",
    "**/vendor/**",
    "**/*.min.js",
]


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> CatalogConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    sources = [
        Source(path=item["path"], source_system=item.get("source_system", "local_laptop"))
        for item in (raw.get("sources") or [])
    ]
    index_code = bool(raw.get("index_code", True))
    exclude = list(raw.get("exclude") or [])
    if index_code:
        # Preserve user order, append defaults, drop duplicates.
        exclude = list(dict.fromkeys(exclude + DEFAULT_CODE_EXCLUDES))
    return CatalogConfig(sources=sources, exclude=exclude, index_code=index_code)


def load_performance_config(
    path: str | Path = DEFAULT_PERFORMANCE_CONFIG_PATH,
) -> PerformanceConfig:
    """Load worker-pool sizes, falling back to defaults when the file is absent."""

    config_path = Path(path)
    if not config_path.exists():
        return PerformanceConfig()
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    defaults = PerformanceConfig()
    return PerformanceConfig(
        extract_workers=int(raw.get("extract_workers", defaults.extract_workers) or 0),
        link_workers=int(raw.get("link_workers", defaults.link_workers) or 0),
        classify_workers=int(
            raw.get("classify_workers", defaults.classify_workers) or 0
        ),
    )


def resolve_workers(flag: int | None, configured: int) -> int:
    """Resolve an effective worker count from a CLI flag and the config value.

    The flag (when given) wins over ``configured``; ``0`` or a negative value at
    either layer means "auto" and resolves to the CPU count. The result is always
    at least 1, so callers can treat ``1`` as the serial path.
    """

    chosen = flag if flag is not None else configured
    if chosen is None or chosen <= 0:
        return os.cpu_count() or 1
    return chosen
