"""Uvicorn launcher for ``catalog api`` / ``navigate api``.

Binds to 127.0.0.1 by default. In reload mode uvicorn needs an import string, so
the app is loaded from ``catalog.api.app:app`` (which reads ``config/api.yml``);
CLI overrides for the database/cache are passed through environment variables so
the reloaded workers pick them up. Without reload, a directly-built application
honoring all overrides is run.
"""

from __future__ import annotations

import os
import sys

from .config import ApiSettings, load_api_config

# Bind addresses that expose the server on every network interface, not just
# loopback. Binding to one of these without an API key means anyone who can reach
# the host can call the API.
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})


def is_wildcard_host(host: str | None) -> bool:
    """True when ``host`` binds to all interfaces rather than loopback."""

    return (host or "").strip() in WILDCARD_HOSTS


def insecure_bind_warning(host: str, settings: ApiSettings) -> str | None:
    """Return a warning when binding ``host`` would expose an unauthenticated API.

    The API is local-first: a wildcard bind (e.g. ``0.0.0.0``) is only safe when
    an API key is required *and* configured. Returns ``None`` when the bind is
    safe so callers can simply test the result.
    """

    if not is_wildcard_host(host):
        return None
    if settings.require_api_key and settings.api_key:
        return None
    return (
        f"SECURITY WARNING: binding to {host} exposes the API on all network "
        "interfaces without an API key. Anyone who can reach this host can read "
        "and modify your knowledge base. Set 'require_api_key: true' in "
        f"config/api.yml and export {settings.api_key_env}, or bind to 127.0.0.1."
    )


def warn_insecure_bind(host: str, settings: ApiSettings) -> None:
    """Print the insecure-bind warning (if any) to stderr."""

    message = insecure_bind_warning(host, settings)
    if message:
        print(message, file=sys.stderr)


def run(
    *,
    host: str | None = None,
    port: int | None = None,
    reload: bool | None = None,
    config_path: str = "config/api.yml",
    db_path: str | None = None,
    cache_dir: str | None = None,
) -> None:
    """Start the API server with optional CLI overrides."""

    import uvicorn

    settings = load_api_config(config_path)
    host = host or settings.host
    port = port if port is not None else settings.port
    reload = settings.reload if reload is None else reload

    # Make the security posture explicit before the server starts accepting
    # connections, not buried in logs after the fact.
    warn_insecure_bind(host, settings)

    # ``settings`` already folds in config and the NAVIGATE_DB/NAVIGATE_CACHE env
    # vars. The shared CLI ``--db``/``--cache`` flags always carry a default, so
    # only treat them as overrides when they differ from that default; otherwise
    # defer to the (config/env-aware) settings.
    defaults = ApiSettings()
    db_path = db_path if (db_path and db_path != defaults.db_path) else settings.db_path
    cache_dir = cache_dir if (cache_dir and cache_dir != defaults.cache_dir) else settings.cache_dir

    # Make overrides visible to app construction (and to reloaded workers).
    os.environ["NAVIGATE_DB"] = db_path
    os.environ["NAVIGATE_CACHE"] = cache_dir

    if reload:
        uvicorn.run("catalog.api.app:app", host=host, port=port, reload=True)
        return

    from .app import create_app

    overridden = ApiSettings(
        **{
            **settings.__dict__,
            "host": host,
            "port": port,
            "db_path": db_path,
            "cache_dir": cache_dir,
        }
    )
    uvicorn.run(create_app(overridden), host=host, port=port)


__all__ = [
    "run",
    "is_wildcard_host",
    "insecure_bind_warning",
    "warn_insecure_bind",
    "WILDCARD_HOSTS",
]
