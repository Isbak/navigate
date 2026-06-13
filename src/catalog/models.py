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
