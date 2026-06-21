"""Configuration for link classification.

Patterns live in ``config/link_patterns.yml`` so users can declare their own
internal domains and extend system domain matching without code changes. The
loader is tolerant: a missing file yields sensible empty defaults so link
discovery still works out of the box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

DEFAULT_LINK_CONFIG_PATH = Path("config/link_patterns.yml")


@dataclass(frozen=True)
class LinkConfig:
    """User-supplied link classification rules.

    ``internal_domains`` are matched against ``host`` and ``host + path`` so an
    entry may scope to a sub-path, e.g. ``dev.azure.com/company``.
    ``systems`` maps a system name to a list of domains that should resolve to
    it, extending the built-in deterministic patterns.
    """

    internal_domains: tuple[str, ...] = ()
    systems: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> LinkConfig:
        return cls()

    def is_internal(self, normalized_url: str) -> bool:
        if not self.internal_domains:
            return False
        parts = urlsplit(normalized_url)
        host = parts.netloc.lower()
        host_path = (host + parts.path).lower()
        return any(
            domain.lower() in host or domain.lower() in host_path
            for domain in self.internal_domains
        )

    def system_for_host(self, host: str) -> str | None:
        host = host.lower()
        for system, domains in self.systems.items():
            if any(domain.lower() in host for domain in domains):
                return system
        return None


def load_link_config(path: str | Path = DEFAULT_LINK_CONFIG_PATH) -> LinkConfig:
    config_path = Path(path)
    if not config_path.exists():
        return LinkConfig.empty()
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    internal = tuple(str(d) for d in raw.get("internal_domains", []) or [])
    systems_raw = raw.get("systems", {}) or {}
    systems = {
        str(name): tuple(str(d) for d in (spec or {}).get("domains", []) or [])
        for name, spec in systems_raw.items()
    }
    return LinkConfig(internal_domains=internal, systems=systems)


__all__ = ["LinkConfig", "load_link_config", "DEFAULT_LINK_CONFIG_PATH"]
