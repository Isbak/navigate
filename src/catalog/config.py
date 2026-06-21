from __future__ import annotations

from pathlib import Path

import yaml

from .models import CatalogConfig, Source

DEFAULT_CONFIG_PATH = Path("config/sources.yml")

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
