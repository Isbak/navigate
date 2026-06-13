"""Knowledge-discovery analytics over the semantic tables.

These functions answer the questions in the success criteria without anyone
reading every document:

* What kinds of documents do I have?      -> :func:`document_types`
* Which capabilities appear most often?   -> :func:`top_capabilities`
* What decisions are repeatedly discussed?-> :func:`decision_themes`
* Which risks occur across documents?     -> :func:`risk_themes`
* Which concepts connect multiple domains?-> :func:`concepts_connecting_domains`

Aggregations that group by a free-text name are case-insensitive and count both
how many times an item was proposed and how many distinct documents proposed it,
so a capability mentioned once in ten documents ranks above one mentioned ten
times in a single document.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict


def document_types(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT document_type AS name, COUNT(*) AS count "
        "FROM document_classifications GROUP BY document_type "
        "ORDER BY count DESC, name"
    ).fetchall()
    return [{"name": r["name"], "count": r["count"]} for r in rows]


def _name_frequency(
    conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()
) -> list[dict]:
    """Group a candidate table by ``name`` (case-insensitive).

    Returns ``[{name, mentions, documents, avg_confidence}]`` ordered by document
    spread then mentions.
    """

    clause = f"WHERE {where}" if where else ""
    rows = conn.execute(
        f"""
        SELECT name AS name,
               COUNT(*) AS mentions,
               COUNT(DISTINCT artifact_id) AS documents,
               AVG(confidence) AS avg_confidence
        FROM {table}
        {clause}
        GROUP BY name COLLATE NOCASE
        ORDER BY documents DESC, mentions DESC, name
        """,
        params,
    ).fetchall()
    return [
        {
            "name": r["name"],
            "mentions": r["mentions"],
            "documents": r["documents"],
            "avg_confidence": round(r["avg_confidence"] or 0.0, 3),
        }
        for r in rows
    ]


def top_capabilities(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return _name_frequency(conn, "candidate_capabilities")[:limit]


def top_technologies(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return _name_frequency(
        conn, "candidate_entities", "entity_type = ?", ("Technology",)
    )[:limit]


def top_concepts(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return _name_frequency(
        conn, "candidate_entities", "entity_type = ?", ("Concept",)
    )[:limit]


def _theme_frequency(
    conn: sqlite3.Connection, table: str, column: str
) -> list[dict]:
    """Group a free-text claim column case-insensitively into themes."""

    rows = conn.execute(
        f"""
        SELECT {column} AS text,
               COUNT(*) AS mentions,
               COUNT(DISTINCT artifact_id) AS documents,
               AVG(confidence) AS avg_confidence
        FROM {table}
        GROUP BY {column} COLLATE NOCASE
        ORDER BY documents DESC, mentions DESC, text
        """
    ).fetchall()
    return [
        {
            "text": r["text"],
            "mentions": r["mentions"],
            "documents": r["documents"],
            "avg_confidence": round(r["avg_confidence"] or 0.0, 3),
        }
        for r in rows
    ]


def decision_themes(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return _theme_frequency(conn, "candidate_decisions", "decision_text")[:limit]


def risk_themes(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return _theme_frequency(conn, "candidate_risks", "risk_description")[:limit]


def _iter_document_domains(conn: sqlite3.Connection):
    """Yield ``(artifact_id, [domain, ...])`` from stored domain JSON."""

    for row in conn.execute(
        "SELECT artifact_id, domains FROM document_classifications"
    ):
        raw = row["domains"]
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        names = []
        for item in parsed if isinstance(parsed, list) else []:
            if isinstance(item, dict) and item.get("domain"):
                names.append(str(item["domain"]))
        yield row["artifact_id"], names


def top_domains(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Rank domains by how many documents mention them.

    Domains are stored as JSON on each classification, so this aggregates in
    Python rather than SQL.
    """

    doc_counts: dict[str, int] = defaultdict(int)
    conf_sum: dict[str, float] = defaultdict(float)
    conf_n: dict[str, int] = defaultdict(int)

    for row in conn.execute("SELECT domains FROM document_classifications"):
        raw = row["domains"]
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        seen: set[str] = set()
        for item in parsed if isinstance(parsed, list) else []:
            if not (isinstance(item, dict) and item.get("domain")):
                continue
            name = str(item["domain"])
            if name not in seen:
                doc_counts[name] += 1
                seen.add(name)
            conf_sum[name] += float(item.get("confidence", 0.0) or 0.0)
            conf_n[name] += 1

    ranked = sorted(
        doc_counts.items(), key=lambda kv: (-kv[1], kv[0].lower())
    )
    out = []
    for name, count in ranked[:limit]:
        avg = conf_sum[name] / conf_n[name] if conf_n[name] else 0.0
        out.append({"name": name, "documents": count, "avg_confidence": round(avg, 3)})
    return out


def concepts_connecting_domains(
    conn: sqlite3.Connection, min_domains: int = 2, limit: int = 20
) -> list[dict]:
    """Concepts whose documents collectively span multiple domains.

    For each Concept entity, gather the domains of every document it appears in;
    a concept linking >= ``min_domains`` distinct domains is a cross-cutting idea
    worth surfacing.
    """

    domains_by_doc = {aid: set(names) for aid, names in _iter_document_domains(conn)}

    concept_domains: dict[str, set[str]] = defaultdict(set)
    concept_docs: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT name, artifact_id FROM candidate_entities WHERE entity_type = 'Concept'"
    ):
        name = row["name"]
        aid = row["artifact_id"]
        concept_docs[name].add(aid)
        concept_domains[name].update(domains_by_doc.get(aid, set()))

    out = []
    for name, domains in concept_domains.items():
        if len(domains) >= min_domains:
            out.append(
                {
                    "name": name,
                    "domains": sorted(domains),
                    "domain_count": len(domains),
                    "documents": len(concept_docs[name]),
                }
            )
    out.sort(key=lambda d: (-d["domain_count"], -d["documents"], d["name"].lower()))
    return out[:limit]


__all__ = [
    "document_types",
    "top_capabilities",
    "top_technologies",
    "top_concepts",
    "top_domains",
    "decision_themes",
    "risk_themes",
    "concepts_connecting_domains",
]
