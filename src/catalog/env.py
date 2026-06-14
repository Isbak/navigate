"""Environment-file loading helpers.

Navigate keeps shareable, non-secret defaults in ``config/*.yml`` and secrets or
machine-local overrides in an ignored ``.env`` file. This module implements the
small subset of dotenv parsing we need without adding a runtime dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ENV_PATH = Path(".env")
_LOADED_PATHS: set[Path] = set()


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.strip()


def _parse_value(value: str) -> str:
    value = _strip_inline_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if "${" in value:
        value = os.path.expandvars(value)
    return value


def load_dotenv(path: str | Path = _DEFAULT_ENV_PATH, *, override: bool = False) -> bool:
    """Load key/value pairs from ``path`` into ``os.environ``.

    Existing process environment values win by default so deployment systems can
    safely inject secrets without being shadowed by a local file. Returns
    ``True`` when a file was found and parsed.
    """

    env_path = Path(path)
    if not env_path.exists():
        return False
    resolved = env_path.resolve()
    if resolved in _LOADED_PATHS and not override:
        return True

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = _parse_value(raw_value)

    _LOADED_PATHS.add(resolved)
    return True


__all__ = ["load_dotenv"]
