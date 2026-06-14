"""Map database rows onto the public Pydantic schemas.

Kept separate from both the routes (which stay thin) and the schemas (which stay
declarative). Each function takes a ``sqlite3.Row`` (or plain dict) and returns
the corresponding schema model.
"""

from __future__ import annotations

import json
from typing import Any

from . import schemas


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return value if value is not None else default


def artifact(
    row: Any, *, extracted_ids: set[str], classified_ids: set[str]
) -> schemas.Artifact:
    art_id = row["id"]
    return schemas.Artifact(
        id=art_id,
        path=row["path"],
        filename=row["filename"],
        file_type=row["file_type"],
        size_bytes=_get(row, "size_bytes"),
        created_at=_get(row, "created_at"),
        modified_at=_get(row, "modified_at"),
        sha256=_get(row, "sha256"),
        source_system=_get(row, "source_system"),
        scan_status=_get(row, "scan_status"),
        first_seen_at=_get(row, "first_seen_at"),
        last_scanned_at=_get(row, "last_scanned_at"),
        extraction_status="EXTRACTED" if art_id in extracted_ids else "PENDING",
        classification_status="CLASSIFIED" if art_id in classified_ids else "UNCLASSIFIED",
    )


def link(row: Any) -> schemas.Link:
    return schemas.Link(
        id=row["id"],
        source_artifact_id=row["source_artifact_id"],
        raw_url=row["raw_url"],
        normalized_url=row["normalized_url"],
        anchor_text=_get(row, "anchor_text"),
        target_system=_get(row, "target_system"),
        target_type=_get(row, "target_type"),
        link_kind=_get(row, "link_kind"),
        discovered_at=_get(row, "discovered_at"),
        last_seen_at=_get(row, "last_seen_at"),
        status=_get(row, "status"),
    )


def knowledge_object(row: Any) -> schemas.KnowledgeObject:
    owner_type = _get(row, "owner_type")
    owner_id = _get(row, "owner_id")
    owner = f"{owner_type}:{owner_id}" if owner_id else None
    return schemas.KnowledgeObject(
        id=row["id"],
        name=row["name"],
        object_type=row["object_type"],
        description=_get(row, "description"),
        canonical_name=_get(row, "canonical_name"),
        confidence=_get(row, "confidence"),
        status=_get(row, "status"),
        merge_confidence=_get(row, "merge_confidence"),
        created_at=_get(row, "created_at"),
        updated_at=_get(row, "updated_at"),
        review_status=_get(row, "review_state"),
        freshness_state=_get(row, "freshness_state"),
        quality_score=_get(row, "quality_score"),
        owner=owner,
        relationship_count=_get(row, "relationship_count"),
        evidence_count=_get(row, "evidence_count"),
        mention_count=_get(row, "mention_count"),
    )


def _parse_evidence(raw: Any) -> Any:
    if not raw:
        return None
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def relationship(row: Any) -> schemas.Relationship:
    return schemas.Relationship(
        id=row["id"],
        source_object=row["source_object"],
        predicate=row["predicate"],
        target_object=row["target_object"],
        confidence=_get(row, "confidence"),
        evidence=_parse_evidence(_get(row, "evidence")),
        review_status=_get(row, "review_status"),
        created_at=_get(row, "created_at"),
        updated_at=_get(row, "updated_at"),
    )


def mention(row: Any) -> schemas.Mention:
    return schemas.Mention(
        id=row["id"],
        knowledge_object_id=row["knowledge_object_id"],
        artifact_id=row["artifact_id"],
        confidence=_get(row, "confidence"),
        source_text=_get(row, "source_text"),
        created_at=_get(row, "created_at"),
    )


def evidence(row: Any) -> schemas.Evidence:
    return schemas.Evidence(
        id=row["id"],
        knowledge_object_id=row["knowledge_object_id"],
        artifact_id=row["artifact_id"],
        quote=_get(row, "quote"),
        page_number=_get(row, "page_number"),
        slide_number=_get(row, "slide_number"),
        confidence=_get(row, "confidence"),
        created_at=_get(row, "created_at"),
    )


def alert(row: Any) -> schemas.GovernanceAlert:
    return schemas.GovernanceAlert(
        id=row["id"],
        alert_type=row["alert_type"],
        severity=row["severity"],
        object_id=_get(row, "object_id"),
        message=_get(row, "message"),
        status=_get(row, "status"),
        created_at=_get(row, "created_at"),
        resolved_at=_get(row, "resolved_at"),
    )


def domain_health(item: dict) -> schemas.DomainHealth:
    return schemas.DomainHealth(
        domain=item["domain"],
        owner=item.get("owner") or None,
        object_count=item["object_count"],
        avg_quality=item["avg_quality"],
        avg_freshness=item["avg_freshness"],
        review_backlog=item["review_backlog"],
    )


def change_entry(row: Any) -> schemas.ChangeLogEntry:
    return schemas.ChangeLogEntry(
        id=row["id"],
        change_type=row["change_type"],
        target_kind=_get(row, "target_kind"),
        object_id=_get(row, "object_id"),
        field=_get(row, "field"),
        old_value=_get(row, "old_value"),
        new_value=_get(row, "new_value"),
        detail=_get(row, "detail"),
        detected_at=_get(row, "detected_at"),
    )


def graph_node(item: dict) -> schemas.GraphNode:
    return schemas.GraphNode(
        id=item["id"],
        label=item["label"],
        type=item["type"],
        confidence=item.get("confidence"),
        status=item.get("status"),
        documents=item.get("documents"),
        mentions=item.get("mentions"),
    )


def graph_edge(item: dict) -> schemas.GraphEdge:
    return schemas.GraphEdge(
        id=item["id"],
        source=item["source"],
        target=item["target"],
        predicate=item["predicate"],
        confidence=item.get("confidence"),
        status=item.get("status"),
    )


def job(row: Any) -> schemas.Job:
    summary = _get(row, "result_summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except (TypeError, ValueError):
            summary = {"raw": summary}
    return schemas.Job(
        id=row["id"],
        job_type=row["job_type"],
        status=row["status"],
        started_at=_get(row, "started_at"),
        completed_at=_get(row, "completed_at"),
        error_message=_get(row, "error_message"),
        result_summary=summary,
        created_at=_get(row, "created_at"),
    )


__all__ = [
    "artifact",
    "link",
    "knowledge_object",
    "relationship",
    "mention",
    "evidence",
    "alert",
    "domain_health",
    "change_entry",
    "graph_node",
    "graph_edge",
    "job",
]
