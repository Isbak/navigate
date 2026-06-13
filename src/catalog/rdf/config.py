"""Apache Jena Fuseki connection configuration (``config/jena.yml``).

The loader is tolerant: a missing file yields sensible localhost defaults so the
RDF export still works out of the box and only ``fuseki-load`` / ``fuseki-clear``
actually need a reachable server.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_JENA_CONFIG_PATH = Path("config/jena.yml")
DEFAULT_ENDPOINT = "http://localhost:3030/knowledge-atlas"
DEFAULT_DATASET = "atlas"


@dataclass(frozen=True)
class FusekiConfig:
    """Where the Fuseki dataset lives.

    ``endpoint`` is the dataset base URL; the SPARQL Update endpoint is derived as
    ``<endpoint>/update``.
    """

    endpoint: str = DEFAULT_ENDPOINT
    dataset: str = DEFAULT_DATASET

    @property
    def update_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/update"

    @classmethod
    def defaults(cls) -> "FusekiConfig":
        return cls()


def load_jena_config(path: str | Path = DEFAULT_JENA_CONFIG_PATH) -> FusekiConfig:
    config_path = Path(path)
    if not config_path.exists():
        return FusekiConfig.defaults()
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    fuseki = raw.get("fuseki", {}) or {}
    return FusekiConfig(
        endpoint=str(fuseki.get("endpoint", DEFAULT_ENDPOINT)),
        dataset=str(fuseki.get("dataset", DEFAULT_DATASET)),
    )


__all__ = [
    "FusekiConfig",
    "load_jena_config",
    "DEFAULT_JENA_CONFIG_PATH",
    "DEFAULT_ENDPOINT",
    "DEFAULT_DATASET",
]
