from __future__ import annotations

from pathlib import Path
import yaml

from .models import CatalogConfig, Source

DEFAULT_CONFIG_PATH = Path("config/sources.yml")


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> CatalogConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    sources = [Source(path=item["path"], source_system=item.get("source_system", "local_laptop")) for item in raw.get("sources", [])]
    return CatalogConfig(sources=sources, exclude=list(raw.get("exclude", [])))
