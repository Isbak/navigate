"""Domain governance.

A *knowledge domain* (Digital Transformation, Architecture, Leadership, Test &
Release, Data, Operations) is a business area the organization manages. Each
domain has an owner (from config), a quality score, a freshness score, and a
review backlog.

Knowledge objects are mapped to domains through their evidence: the documents
that mention an object carry domain classifications (from the semantic layer), so
an object belongs to the domains its source documents were classified under. The
domain's aggregate metrics are then averaged over the objects that belong to it.
"""

from __future__ import annotations

import json
import sqlite3

from . import repository as repo
from .config import GovernanceConfig
from .models import OPEN_REVIEW_STATES


def _object_domains(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Map object_id -> set of domain names, via its mentions' documents."""

    rows = conn.execute(
        """
        SELECT DISTINCT m.knowledge_object_id AS object_id, c.domains AS domains
        FROM knowledge_mentions m
        JOIN document_classifications c ON c.artifact_id = m.artifact_id
        WHERE c.domains IS NOT NULL AND c.domains != ''
        """
    ).fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        try:
            parsed = json.loads(r["domains"])
        except (TypeError, ValueError):
            continue
        for entry in parsed if isinstance(parsed, list) else []:
            name = (entry.get("domain") if isinstance(entry, dict) else None) or ""
            if name.strip():
                out.setdefault(r["object_id"], set()).add(name.strip())
    return out


def domain_health(
    conn: sqlite3.Connection, config: GovernanceConfig
) -> list[dict]:
    """Per-domain object count, owner, average quality and freshness, and backlog.

    Every configured domain appears even with zero objects, so a domain with no
    coverage is itself visible (a governance signal in its own right).
    """

    obj_domains = _object_domains(conn)
    quality = repo.quality_map(conn)
    lifecycle = repo.lifecycle_map(conn)

    # Invert to domain -> [object_ids].
    by_domain: dict[str, list[str]] = {}
    for object_id, names in obj_domains.items():
        for name in names:
            by_domain.setdefault(name, []).append(object_id)

    # Ensure every configured domain is represented.
    domain_names = {d.name for d in config.domains} | set(by_domain)

    results: list[dict] = []
    for name in sorted(domain_names):
        members = by_domain.get(name, [])
        qualities = [
            quality[o]["quality_score"] for o in members if o in quality
        ]
        freshness = [
            lifecycle[o]["freshness_score"]
            for o in members
            if o in lifecycle and lifecycle[o]["freshness_score"] is not None
        ]
        backlog = sum(
            1
            for o in members
            if o in lifecycle and lifecycle[o]["review_state"] in OPEN_REVIEW_STATES
        )
        results.append(
            {
                "domain": name,
                "owner": config.domain_owner(name),
                "object_count": len(members),
                "avg_quality": round(sum(qualities) / len(qualities), 1) if qualities else 0.0,
                "avg_freshness": round(sum(freshness) / len(freshness), 3) if freshness else 0.0,
                "review_backlog": backlog,
            }
        )
    results.sort(key=lambda d: (-d["object_count"], d["domain"]))
    return results


__all__ = ["domain_health"]
