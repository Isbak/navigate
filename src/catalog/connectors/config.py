"""Connector configuration: loading and env-var expansion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: str) -> str:
    """Replace ``${VAR}`` placeholders with the corresponding env var (empty string if unset)."""
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _expand(obj: object) -> object:
    """Recursively expand env-var placeholders in all string values of a nested structure."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    return obj


@dataclass(frozen=True)
class ConnectorEntry:
    """One connector as parsed from ``config/connectors.yml``."""

    name: str
    type: str
    credentials: dict
    enabled: bool
    settings: dict  # type-specific keys (repos, folders, spaces, projects, ...)


@dataclass(frozen=True)
class ConnectorsConfig:
    connectors: list[ConnectorEntry]
    cache_dir: str = "connector_cache"


def load_connectors_config(path: str | Path = "config/connectors.yml") -> ConnectorsConfig:
    """Load and parse the connectors config, expanding ``${ENV_VAR}`` placeholders."""

    p = Path(path)
    if not p.exists():
        return ConnectorsConfig(connectors=[], cache_dir="connector_cache")

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"connectors config must be a YAML mapping: {p}")

    expanded = _expand(raw)
    assert isinstance(expanded, dict)
    cache_dir = str(expanded.get("cache_dir", "connector_cache"))

    entries: list[ConnectorEntry] = []
    for item in expanded.get("connectors", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        type_ = str(item.get("type", "")).strip()
        if not name or not type_:
            continue
        creds: dict = item.get("credentials", {}) or {}
        enabled = bool(item.get("enabled", True))
        settings = {
            k: v for k, v in item.items()
            if k not in {"name", "type", "credentials", "enabled"}
        }
        entries.append(ConnectorEntry(
            name=name, type=type_, credentials=creds,
            enabled=enabled, settings=settings,
        ))

    return ConnectorsConfig(connectors=entries, cache_dir=cache_dir)
