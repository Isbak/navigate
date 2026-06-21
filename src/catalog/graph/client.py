"""``GraphClient`` - SPARQL execution and the on-disk query library.

The client has two interchangeable backends:

* **local** - an in-memory :class:`rdflib.Graph` (built from SQLite via
  :func:`catalog.graph.loader.build_graph`). SPARQL runs through rdflib's own
  engine. This is the default: offline, deterministic, and exactly what tests
  and CI use, since it needs no running server.
* **remote** - a live Apache Jena Fuseki dataset. ``execute_query`` POSTs the
  SPARQL to the dataset's ``/sparql`` endpoint and parses the standard
  ``application/sparql-results+json`` response. The HTTP call is injectable
  (``fetcher=``) so the remote path is testable without the network.

Both backends return the same shape: a list of ``{variable: value}`` dicts where
values are plain strings (URIs as their full IRI, literals as their lexical
form) or ``None`` for an unbound optional. Rendering/abbreviation is the
caller's job.

The query library lives in ``queries/`` as ``*.rq`` files; ``list_queries`` /
``load_query`` / ``save_query`` manage it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rdflib import Graph

DEFAULT_QUERIES_DIR = "queries"
QUERY_SUFFIX = ".rq"

# A fetcher takes (query_url, sparql) and returns the parsed SPARQL-JSON dict.
Fetcher = Callable[[str, str], dict]


class QueryError(RuntimeError):
    """Raised when a SPARQL query cannot be executed or a query file is missing."""


def _default_fetcher(query_url: str, sparql: str) -> dict:
    """POST a SPARQL query to a Fuseki endpoint and return parsed JSON results."""

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a declared dep
        raise QueryError(
            "The 'requests' package is required to query a remote Fuseki endpoint."
        ) from exc

    try:
        response = requests.post(
            query_url,
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:  # connection refused, HTTP errors, ...
        raise QueryError(f"Failed to query Fuseki at {query_url}: {exc}") from exc


class GraphClient:
    """Execute SPARQL against a local rdflib graph or a remote Fuseki dataset."""

    def __init__(
        self,
        *,
        graph: Graph | None = None,
        query_url: str | None = None,
        fetcher: Fetcher | None = None,
        queries_dir: str | Path = DEFAULT_QUERIES_DIR,
    ) -> None:
        if graph is None and query_url is None:
            raise ValueError("GraphClient needs either a graph or a query_url.")
        self._graph = graph
        self._query_url = query_url
        self._fetcher = fetcher or _default_fetcher
        self.queries_dir = Path(queries_dir)

    # -- construction helpers -------------------------------------------------

    @classmethod
    def from_graph(
        cls, graph: Graph, *, queries_dir: str | Path = DEFAULT_QUERIES_DIR
    ) -> GraphClient:
        return cls(graph=graph, queries_dir=queries_dir)

    @classmethod
    def from_sqlite(
        cls, conn, *, queries_dir: str | Path = DEFAULT_QUERIES_DIR
    ) -> GraphClient:
        """Build the in-memory approved graph from a SQLite connection."""

        from .loader import build_graph

        return cls(graph=build_graph(conn), queries_dir=queries_dir)

    @classmethod
    def from_fuseki(
        cls,
        config,
        *,
        fetcher: Fetcher | None = None,
        queries_dir: str | Path = DEFAULT_QUERIES_DIR,
    ) -> GraphClient:
        return cls(query_url=config.query_url, fetcher=fetcher, queries_dir=queries_dir)

    @property
    def is_remote(self) -> bool:
        return self._graph is None

    # -- query execution ------------------------------------------------------

    def execute_query(self, sparql: str) -> list[dict]:
        """Run a SPARQL SELECT/ASK and return a list of binding dicts."""

        if self._graph is not None:
            return self._run_local(sparql)
        return self._run_remote(sparql)

    def _run_local(self, sparql: str) -> list[dict]:
        try:
            result = self._graph.query(sparql)
        except Exception as exc:  # rdflib raises many parse/eval error types
            raise QueryError(f"SPARQL error: {exc}") from exc

        if result.type == "ASK":
            return [{"ask": str(bool(result.askAnswer))}]

        variables = [str(v) for v in (result.vars or [])]
        rows: list[dict] = []
        for record in result:
            row: dict = {}
            for var in variables:
                value = record[var]
                row[var] = None if value is None else str(value)
            rows.append(row)
        return rows

    def _run_remote(self, sparql: str) -> list[dict]:
        payload = self._fetcher(self._query_url, sparql)
        if "boolean" in payload:  # ASK
            return [{"ask": str(bool(payload["boolean"]))}]
        head = payload.get("head", {}).get("vars", [])
        bindings = payload.get("results", {}).get("bindings", [])
        rows: list[dict] = []
        for binding in bindings:
            row = dict.fromkeys(head)
            for var, cell in binding.items():
                row[var] = cell.get("value")
            rows.append(row)
        return rows

    # -- query library --------------------------------------------------------

    def _path_for(self, name: str) -> Path:
        stem = name[:-len(QUERY_SUFFIX)] if name.endswith(QUERY_SUFFIX) else name
        return self.queries_dir / f"{stem}{QUERY_SUFFIX}"

    def list_queries(self) -> list[str]:
        """Return the names (without ``.rq``) of every saved query, sorted."""

        if not self.queries_dir.exists():
            return []
        return sorted(p.stem for p in self.queries_dir.glob(f"*{QUERY_SUFFIX}"))

    def load_query(self, name: str) -> str:
        """Read a saved query's text. Raises ``QueryError`` if it is missing."""

        path = self._path_for(name)
        if not path.exists():
            available = ", ".join(self.list_queries()) or "(none)"
            raise QueryError(
                f"No query named {name!r} in {self.queries_dir}. Available: {available}"
            )
        return path.read_text(encoding="utf-8")

    def save_query(self, name: str, text: str) -> Path:
        """Write (or overwrite) a query file. Returns its path."""

        path = self._path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = text if text.endswith("\n") else text + "\n"
        path.write_text(body, encoding="utf-8")
        return path

    def run_named_query(self, name: str) -> list[dict]:
        """Convenience: load a saved query by name and execute it."""

        return self.execute_query(self.load_query(name))


__all__ = [
    "GraphClient",
    "QueryError",
    "Fetcher",
    "DEFAULT_QUERIES_DIR",
    "QUERY_SUFFIX",
]
