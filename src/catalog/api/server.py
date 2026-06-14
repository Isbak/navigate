"""Uvicorn launcher for ``catalog api`` / ``navigate api``.

Binds to 127.0.0.1 by default. In reload mode uvicorn needs an import string, so
the app is loaded from ``catalog.api.app:app`` (which reads ``config/api.yml``);
CLI overrides for the database/cache are passed through environment variables so
the reloaded workers pick them up. Without reload, a directly-built application
honoring all overrides is run.
"""

from __future__ import annotations

import os

from .config import ApiSettings, load_api_config


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

    # ``settings`` already folds in config and the NAVIGATE_DB/NAVIGATE_CACHE env
    # vars. The shared CLI ``--db``/``--cache`` flags always carry a default, so
    # only treat them as overrides when they differ from that default; otherwise
    # defer to the (config/env-aware) settings.
    defaults = ApiSettings()
    db_path = db_path if (db_path and db_path != defaults.db_path) else settings.db_path
    cache_dir = (
        cache_dir if (cache_dir and cache_dir != defaults.cache_dir) else settings.cache_dir
    )

    # Make overrides visible to app construction (and to reloaded workers).
    os.environ["NAVIGATE_DB"] = db_path
    os.environ["NAVIGATE_CACHE"] = cache_dir

    if reload:
        uvicorn.run("catalog.api.app:app", host=host, port=port, reload=True)
        return

    from .app import create_app

    overridden = ApiSettings(
        **{**settings.__dict__, "host": host, "port": port, "db_path": db_path, "cache_dir": cache_dir}
    )
    uvicorn.run(create_app(overridden), host=host, port=port)


__all__ = ["run"]
