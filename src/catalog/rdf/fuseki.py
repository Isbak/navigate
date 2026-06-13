"""Load the RDF projection into Apache Jena Fuseki via SPARQL Update.

The loader reads the exported ``*.ttl`` files, validates that each still parses,
then uploads them in dependency order (ontology first) by wrapping each graph's
triples in a SPARQL ``INSERT DATA { ... }`` request against the dataset's
``/update`` endpoint. ``fuseki-clear`` issues ``CLEAR ALL``.

HTTP is done with the standard library so the package adds no runtime
dependency beyond rdflib. The actual POST is injectable (``poster=``) so tests
exercise the full workflow without a running server, and so do not touch the
network.

Fuseki remains a *query layer*: nothing here writes back to SQLite, and the
upload is always reproducible from ``catalog rdf-export``.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from rdflib import Graph

from .config import FusekiConfig
from .export import DEFAULT_OUT_DIR, FORMATS, validate_rdf

# Upload order matters: the schema is loaded before the instance data that uses
# it, and provenance last because it references the knowledge resources.
LOAD_ORDER = ("ontology", "knowledge", "relationships", "provenance")

# A poster takes (url, body_bytes, content_type) and performs the POST. The
# default hits the network; tests pass a recording stub.
Poster = Callable[[str, bytes, str], None]


class FusekiError(RuntimeError):
    """Raised when a Fuseki request fails."""


def _default_poster(url: str, body: bytes, content_type: str) -> None:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status >= 300:
                raise FusekiError(f"{url} returned HTTP {response.status}")
    except urllib.error.URLError as exc:  # connection refused, timeouts, HTTP 4xx/5xx
        raise FusekiError(f"Failed to reach Fuseki at {url}: {exc}") from exc


def _insert_data_query(graph: Graph) -> str:
    """Wrap a graph's triples in a SPARQL ``INSERT DATA`` request.

    N-Triples are valid inside a SPARQL group graph pattern, so the graph is
    serialized to N-Triples and embedded directly.
    """

    triples = graph.serialize(format="nt")
    return f"INSERT DATA {{\n{triples}}}\n"


def upload_graph(
    config: FusekiConfig, graph: Graph, *, poster: Poster = _default_poster
) -> int:
    """Upload one graph via SPARQL Update. Returns the number of triples sent."""

    if len(graph) == 0:
        return 0
    poster(config.update_url, _insert_data_query(graph).encode("utf-8"),
           "application/sparql-update")
    return len(graph)


def clear_dataset(config: FusekiConfig, *, poster: Poster = _default_poster) -> None:
    """Remove all triples from the dataset (``CLEAR ALL``)."""

    poster(config.update_url, b"CLEAR ALL", "application/sparql-update")


def _valid_extensions() -> set[str]:
    return {ext for _, ext in FORMATS.values()}


def fuseki_load(
    config: FusekiConfig,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    *,
    poster: Poster = _default_poster,
) -> dict[str, int]:
    """Validate the exported files then upload them in dependency order.

    Returns ``{graph_name: triples_uploaded}``. Raises ``FusekiError`` if a file
    is missing or fails to validate before any upload is attempted.
    """

    out = Path(out_dir)

    # 1. Validate first - never upload something that does not parse.
    validation = validate_rdf(out)
    if not validation:
        raise FusekiError(
            f"No RDF files found in {out}. Run: catalog rdf-export"
        )
    bad = {name: r["error"] for name, r in validation.items() if not r["ok"]}
    if bad:
        details = "; ".join(f"{n}: {e}" for n, e in bad.items())
        raise FusekiError(f"Validation failed: {details}")

    # 2..5. Upload ontology, knowledge, relationships, provenance.
    uploaded: dict[str, int] = {}
    for name in LOAD_ORDER:
        path = _find_file(out, name)
        if path is None:
            continue
        graph = Graph()
        graph.parse(str(path))
        uploaded[name] = upload_graph(config, graph, poster=poster)
    return uploaded


def _find_file(out_dir: Path, stem: str) -> Path | None:
    for ext in _valid_extensions():
        candidate = out_dir / f"{stem}.{ext}"
        if candidate.exists():
            return candidate
    return None


__all__ = [
    "FusekiError",
    "Poster",
    "LOAD_ORDER",
    "upload_graph",
    "clear_dataset",
    "fuseki_load",
]
