"""The ``doctor`` command: a one-shot health check of a Navigate installation.

Inspects configuration, the database, the cache, LLM credentials, the Fuseki/RDF
endpoint, and filesystem permissions, then prints an ``OK`` / ``WARN`` / ``FAIL``
report. It reuses the same loaders the real commands use so the report reflects
what those commands would actually see. Read-only: it never writes to the
database or mutates configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from ..api.config import load_api_config
from ..api.server import insecure_bind_warning
from ..db import DatabaseNotWritableError, connect
from ..env import load_dotenv
from ..rdf.config import load_jena_config
from ..semantic.config import load_llm_config

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

# Env var that holds each provider's API key (Ollama runs locally, no key).
_PROVIDER_KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _config_paths(args: argparse.Namespace) -> dict[str, str]:
    """Map each config flag present on ``args`` to a friendly label."""

    mapping = {
        "config": "sources",
        "link_config": "link_patterns",
        "llm_config": "llm",
        "extract_config": "extraction",
        "jena_config": "jena",
        "governance_config": "governance",
        "compliance_config": "compliance",
        "performance_config": "performance",
        "api_config": "api",
    }
    return {
        label: getattr(args, attr) for attr, label in mapping.items() if getattr(args, attr, None)
    }


def _check_config(args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    for label, path in _config_paths(args).items():
        p = Path(path)
        if not p.exists():
            # Every loader falls back to safe defaults, except sources.yml which
            # scan/consolidate need.
            status = WARN if label == "sources" else OK
            detail = (
                "missing; using built-in defaults" if status == OK else "missing; scan needs it"
            )
            checks.append(Check(f"config:{label}", status, f"{path} {detail}"))
            continue
        try:
            yaml.safe_load(p.read_text(encoding="utf-8"))
            checks.append(Check(f"config:{label}", OK, f"{path} parses"))
        except yaml.YAMLError as exc:
            checks.append(Check(f"config:{label}", FAIL, f"{path} is not valid YAML: {exc}"))
    return checks


def _check_database(args: argparse.Namespace) -> Check:
    db = Path(args.db)
    if not db.exists():
        parent = db.parent if str(db.parent) else Path(".")
        if parent.exists() and os.access(parent, os.W_OK):
            return Check("database", WARN, f"{args.db} not initialized; run: catalog init-db")
        return Check("database", FAIL, f"{args.db} missing and {parent}/ is not writable")
    try:
        with connect(args.db) as conn:
            conn.execute("SELECT 1").fetchone()
        writable = os.access(db, os.W_OK)
        if not writable:
            return Check("database", WARN, f"{args.db} is read-only")
        return Check("database", OK, f"{args.db} connects and is writable")
    except DatabaseNotWritableError as exc:
        return Check("database", FAIL, str(exc))
    except sqlite3.Error as exc:
        return Check("database", FAIL, f"{args.db}: {exc}")


def _check_cache(args: argparse.Namespace) -> Check:
    cache = Path(args.cache)
    if not cache.exists():
        return Check(
            "cache", WARN, f"{args.cache} does not exist yet; it will be created on first use"
        )
    if not cache.is_dir():
        return Check("cache", FAIL, f"{args.cache} exists but is not a directory")
    if not os.access(cache, os.W_OK):
        return Check("cache", FAIL, f"{args.cache} is not writable")
    return Check("cache", OK, f"{args.cache} exists and is writable")


def _check_llm(args: argparse.Namespace) -> Check:
    load_dotenv()
    try:
        config = load_llm_config(args.llm_config)
    except Exception as exc:  # noqa: BLE001 - report any loader failure as a check
        return Check("llm", FAIL, f"could not load {args.llm_config}: {exc}")
    provider = config.provider
    if provider not in _PROVIDER_KEY_ENV:
        # Local provider (e.g. Ollama) needs no API key.
        return Check(
            "llm", OK, f"provider '{provider}' (model {config.model}); no API key required"
        )
    env_name = config.options.get("api_key_env", _PROVIDER_KEY_ENV[provider])
    if os.environ.get(env_name):
        return Check("llm", OK, f"provider '{provider}'; {env_name} is set")
    return Check(
        "llm",
        WARN,
        f"provider '{provider}' needs an API key but {env_name} is not set "
        "(export it or add it to .env)",
    )


def _check_fuseki(args: argparse.Namespace) -> Check:
    config = load_jena_config(args.jena_config)
    try:
        import requests

        resp = requests.get(
            config.query_url,
            params={"query": "ASK{}"},
            timeout=2,
            headers={"Accept": "application/sparql-results+json"},
        )
        if resp.status_code < 500:
            return Check("fuseki", OK, f"reachable at {config.endpoint}")
        return Check("fuseki", WARN, f"{config.endpoint} returned HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 - network failures are expected when Fuseki is off
        return Check(
            "fuseki",
            WARN,
            f"not reachable at {config.endpoint} ({exc.__class__.__name__}); "
            "only needed for fuseki-load/clear",
        )


def _check_security(args: argparse.Namespace) -> Check:
    settings = load_api_config(getattr(args, "api_config", "config/api.yml"))
    warning = insecure_bind_warning(settings.host, settings)
    if warning:
        return Check("security", WARN, warning)
    return Check("security", OK, f"API binds to {settings.host} (loopback or API-key protected)")


def _run_checks(args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    checks.extend(_check_config(args))
    checks.append(_check_database(args))
    checks.append(_check_cache(args))
    checks.append(_check_llm(args))
    checks.append(_check_fuseki(args))
    checks.append(_check_security(args))
    return checks


def _cmd_doctor(args: argparse.Namespace) -> int:
    checks = _run_checks(args)

    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        print("Navigate doctor — environment health check\n")
        for c in checks:
            print(f"  {c.status:<4} {c.name}: {c.detail}")
        n_fail = sum(1 for c in checks if c.status == FAIL)
        n_warn = sum(1 for c in checks if c.status == WARN)
        print(f"\n{len(checks)} checks: {n_fail} failed, {n_warn} warnings.")

    if any(c.status == FAIL for c in checks):
        return 1
    if args.strict and any(c.status == WARN for c in checks):
        return 1
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    doctor = sub.add_parser(
        "doctor",
        help="check config, database, cache, LLM keys, Fuseki and permissions",
    )
    doctor.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on warnings as well as failures",
    )
    doctor.add_argument("--json", action="store_true", help="emit the report as JSON")
    doctor.add_argument(
        "--api-config",
        default="config/api.yml",
        help="API config to inspect for the insecure-bind check",
    )
    doctor.set_defaults(func=_cmd_doctor)


__all__ = ["register"]
