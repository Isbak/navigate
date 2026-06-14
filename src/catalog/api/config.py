"""Configuration for the REST API.

Loads ``config/api.yml`` into a typed :class:`ApiSettings`, falling back to safe,
local-first defaults for any missing file or key so the API always starts. The
defaults bind to ``127.0.0.1`` and never enable an API key or external calls -
those must be opted into explicitly.

Both layouts are accepted: a flat file (``host: ...``) and keys nested under an
``api:`` section, so the snippet in the project README works verbatim. Database
and cache locations can also be overridden by the ``NAVIGATE_DB`` /
``NAVIGATE_CACHE`` environment variables, which is how the CLI propagates its
flags into a reload-mode server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config/api.yml")

# Local development origins allowed by CORS out of the box. Deliberately narrow:
# the API is local-first and should not be reachable from arbitrary origins.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


@dataclass(frozen=True)
class ApiSettings:
    """Resolved REST API configuration."""

    host: str = "127.0.0.1"
    port: int = 8000
    reload: bool = True
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    require_api_key: bool = False
    api_key_env: str = "NAVIGATE_API_KEY"
    enable_graphrag: bool = False
    enable_classify: bool = False
    db_path: str = "data/catalog.sqlite"
    cache_dir: str = "cache"
    queries_dir: str = "queries"
    sources_config: str = "config/sources.yml"
    link_config: str = "config/link_patterns.yml"
    llm_config: str = "config/llm.yml"
    governance_config: str = "config/governance.yml"
    jena_config: str = "config/jena.yml"

    @property
    def api_key(self) -> str | None:
        """The configured API key, read from the environment at access time."""

        value = os.environ.get(self.api_key_env)
        return value.strip() if value and value.strip() else None


def _coerce_origins(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v).strip())
    return DEFAULT_CORS_ORIGINS


def _config_base_dir(path: Path) -> Path:
    """Return the base directory for relative paths in an API config file.

    Repository configs conventionally live in a ``config/`` directory while paths
    such as ``data/catalog.sqlite`` are expressed relative to the repository
    root. For configs elsewhere, resolve relative paths next to the config file.
    """

    parent = path.resolve().parent
    return parent.parent if parent.name == "config" else parent


def _resolve_path(value: object, default: str, base_dir: Path) -> str:
    raw = str(value if value is not None else default)
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if expanded.is_absolute():
        return str(expanded)
    return str((base_dir / expanded).resolve())


def load_api_config(path: str | Path = DEFAULT_CONFIG_PATH) -> ApiSettings:
    """Load API settings, returning all-defaults if the file is absent."""

    data: dict = {}
    p = Path(path)
    config_exists = p.exists()
    base_dir = _config_base_dir(p) if config_exists else Path.cwd()
    if config_exists:
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    # Accept keys nested under an ``api:`` section as well as a flat layout.
    section = data.get("api")
    if isinstance(section, dict):
        merged = {**{k: v for k, v in data.items() if k != "api"}, **section}
    else:
        merged = data

    defaults = ApiSettings()
    settings = ApiSettings(
        host=str(merged.get("host", defaults.host)),
        port=int(merged.get("port", defaults.port)),
        reload=bool(merged.get("reload", defaults.reload)),
        cors_origins=_coerce_origins(merged.get("cors_origins", DEFAULT_CORS_ORIGINS)),
        require_api_key=bool(merged.get("require_api_key", defaults.require_api_key)),
        api_key_env=str(merged.get("api_key_env", defaults.api_key_env)),
        enable_graphrag=bool(merged.get("enable_graphrag", defaults.enable_graphrag)),
        enable_classify=bool(merged.get("enable_classify", defaults.enable_classify)),
        db_path=_resolve_path(
            os.environ.get("NAVIGATE_DB") or merged.get("db_path"),
            defaults.db_path,
            base_dir,
        ),
        cache_dir=_resolve_path(
            os.environ.get("NAVIGATE_CACHE") or merged.get("cache_dir"),
            defaults.cache_dir,
            base_dir,
        ),
        queries_dir=_resolve_path(merged.get("queries_dir"), defaults.queries_dir, base_dir),
        sources_config=_resolve_path(
            merged.get("sources_config"), defaults.sources_config, base_dir
        ),
        link_config=_resolve_path(merged.get("link_config"), defaults.link_config, base_dir),
        llm_config=_resolve_path(merged.get("llm_config"), defaults.llm_config, base_dir),
        governance_config=_resolve_path(
            merged.get("governance_config"), defaults.governance_config, base_dir
        ),
        jena_config=_resolve_path(merged.get("jena_config"), defaults.jena_config, base_dir),
    )
    return settings


__all__ = [
    "ApiSettings",
    "load_api_config",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CORS_ORIGINS",
    "_resolve_path",
]
