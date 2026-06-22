"""Policy-bounded agent approval, and the human undo for it.

This is the one place an AI agent is allowed to *change* the knowledge graph, and
it stays inside three guard rails so governance is preserved:

* **Bounded** — :func:`agent_approve` only touches ``PROPOSED`` objects and
  relationships that fall inside the configured confidence window and pass the
  configured eligibility filters (evidence present, type/predicate allowlists),
  capped at ``max_per_run`` per pass. The agent never picks these — they come
  from :class:`~catalog.governance.config.AgentReviewConfig`.
* **Attributable** — every decision reuses the normal ``APPROVED`` state but is
  tagged ``agent:<name>`` in the reviewer column (see
  :mod:`catalog.governance.provenance`), so it is filterable everywhere.
* **Reversible** — :func:`revert_review` undoes a single decision back to the
  prior state recorded in the audit trail, and :func:`revert_agent_actions` rolls
  back a whole batch by agent name / time window, refusing to clobber any later
  human decision.

It deliberately reuses the existing review primitives (``approve_object``,
``apply_object_state``, ``review_relationship``) rather than writing its own
status logic, so an agent decision is indistinguishable from a human one except
for the reviewer tag and is captured by the same audit tables.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..db import connect, init_db
from ..knowledge import repository as know_repo
from ..knowledge import service as know_service
from ..knowledge.models import ReviewState
from . import service as gov_service
from .config import AgentReviewConfig
from .models import ReviewWorkflowState
from .provenance import AGENT_PREFIX, agent_reviewer, is_agent_reviewer

_OBJECT_DEFAULT_STATE = ReviewWorkflowState.PENDING_REVIEW.value
_RELATIONSHIP_DEFAULT_STATE = ReviewState.PROPOSED.value


@dataclass
class AgentApprovalStats:
    """Outcome of one :func:`agent_approve` pass."""

    reviewer: str = ""
    dry_run: bool = False
    objects_approved: int = 0
    relationships_approved: int = 0
    objects_skipped: int = 0
    relationships_skipped: int = 0
    candidates: list[dict] = field(default_factory=list)

    @property
    def total_approved(self) -> int:
        return self.objects_approved + self.relationships_approved

    def as_dict(self) -> dict:
        return {
            "reviewer": self.reviewer,
            "dry_run": self.dry_run,
            "objects_approved": self.objects_approved,
            "relationships_approved": self.relationships_approved,
            "objects_skipped": self.objects_skipped,
            "relationships_skipped": self.relationships_skipped,
            "total_approved": self.total_approved,
            "candidates": self.candidates,
        }


@dataclass
class RevertResult:
    """Outcome of reverting one target."""

    reverted: bool
    target_kind: str
    target_id: str
    from_state: str = ""
    to_state: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "reverted": self.reverted,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "reason": self.reason,
        }


@dataclass
class RevertStats:
    """Outcome of a batch :func:`revert_agent_actions`."""

    reverted: int = 0
    skipped: int = 0
    results: list[RevertResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "reverted": self.reverted,
            "skipped": self.skipped,
            "results": [r.as_dict() for r in self.results],
        }


def _has_evidence(raw: object) -> bool:
    """True if a relationship's ``evidence`` column holds at least one quote."""

    if not raw:
        return False
    try:
        data = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return bool(str(raw).strip())
    return bool(data)


def _in_window(confidence: float | None, config: AgentReviewConfig) -> bool:
    return (
        confidence is not None
        and config.min_confidence <= float(confidence) <= config.max_confidence
    )


def object_eligible(conn, row, config: AgentReviewConfig) -> tuple[bool, str]:
    """Whether one object may be agent-approved, with a human-readable reason."""

    if not _in_window(row["confidence"], config):
        return False, "confidence outside the policy window"
    if config.allowed_object_types and row["object_type"] not in set(
        config.allowed_object_types
    ):
        return False, f"object type {row['object_type']!r} not in the allowlist"
    if config.require_evidence and not know_repo.evidence_for_object(conn, row["id"]):
        return False, "no supporting evidence"
    return True, ""


def relationship_eligible(conn, row, config: AgentReviewConfig) -> tuple[bool, str]:
    """Whether one relationship may be agent-approved, with a reason."""

    if not _in_window(row["confidence"], config):
        return False, "confidence outside the policy window"
    if config.allowed_predicates and row["predicate"] not in set(
        config.allowed_predicates
    ):
        return False, f"predicate {row['predicate']!r} not in the allowlist"
    if config.require_evidence and not _has_evidence(row["evidence"]):
        return False, "no supporting evidence"
    return True, ""


def agent_approve(
    db_path: str | Path,
    *,
    config: AgentReviewConfig,
    target: str = "all",
    note: str = "",
    dry_run: bool = False,
) -> AgentApprovalStats:
    """Approve eligible ``PROPOSED`` items under the agent-review policy.

    ``target`` selects ``"objects"``, ``"relationships"``, or ``"all"``. With
    ``dry_run=True`` nothing is written — the returned ``candidates`` list shows
    exactly what would be approved, which is the safe way to preview a pass.
    """

    if target not in ("objects", "relationships", "all"):
        raise ValueError(f"target must be objects|relationships|all, got {target!r}")

    reviewer = agent_reviewer(config.agent_name)
    stats = AgentApprovalStats(reviewer=reviewer, dry_run=dry_run)

    init_db(db_path)
    with connect(db_path) as conn:
        object_rows: list = []
        relationship_rows: list = []
        if target in ("objects", "all"):
            for row in know_repo.objects_in_confidence_interval(
                conn, config.min_confidence, config.max_confidence, status="PROPOSED"
            ):
                ok, _reason = object_eligible(conn, row, config)
                if ok:
                    object_rows.append(row)
                else:
                    stats.objects_skipped += 1
        if target in ("relationships", "all"):
            for row in know_repo.relationships_in_confidence_interval(
                conn, config.min_confidence, config.max_confidence, review_status="PROPOSED"
            ):
                ok, _reason = relationship_eligible(conn, row, config)
                if ok:
                    relationship_rows.append(row)
                else:
                    stats.relationships_skipped += 1

    # Blast-radius cap: at most ``max_per_run`` approvals per pass, objects first.
    cap = max(0, config.max_per_run)
    eligible_objects = object_rows[:cap]
    eligible_relationships = relationship_rows[: max(0, cap - len(eligible_objects))]
    stats.objects_skipped += len(object_rows) - len(eligible_objects)
    stats.relationships_skipped += len(relationship_rows) - len(eligible_relationships)

    for row in eligible_objects:
        stats.candidates.append(
            {
                "kind": "object",
                "id": row["id"],
                "label": row["canonical_name"] or row["name"],
                "confidence": row["confidence"],
            }
        )
        if not dry_run and gov_service.approve_object(
            db_path, row["id"], reviewer=reviewer, note=note
        ):
            stats.objects_approved += 1

    for row in eligible_relationships:
        stats.candidates.append(
            {
                "kind": "relationship",
                "id": row["id"],
                "label": f"{row['source_object']} {row['predicate']} {row['target_object']}",
                "confidence": row["confidence"],
            }
        )
        if not dry_run and know_service.review_relationship(
            db_path, row["id"], ReviewState.APPROVED.value, reviewer=reviewer, note=note
        ):
            stats.relationships_approved += 1

    return stats


def revert_review(
    db_path: str | Path,
    target_kind: str,
    target_id: str,
    *,
    reviewer: str = "cli",
    note: str = "",
) -> RevertResult:
    """Undo the most recent review decision on one target, back to its prior state.

    The prior state is the previous entry in the audit trail (or the natural
    default — ``PENDING_REVIEW`` for objects, ``PROPOSED`` for relationships — if
    the agent's was the only decision). The revert is itself recorded as a new,
    human-attributed review event, so the history shows current -> prior.
    """

    if target_kind not in ("object", "relationship"):
        raise ValueError(f"target_kind must be object|relationship, got {target_kind!r}")

    init_db(db_path)
    target_id = str(target_id)
    with connect(db_path) as conn:
        history = know_repo.reviews_for_target(conn, target_kind, target_id)
    if not history:
        return RevertResult(
            False, target_kind, target_id, reason="no review history to revert"
        )

    latest = history[-1]
    if len(history) >= 2:
        prior_state = history[-2]["action"]
    else:
        prior_state = (
            _OBJECT_DEFAULT_STATE
            if target_kind == "object"
            else _RELATIONSHIP_DEFAULT_STATE
        )
    revert_note = note or f"revert of {latest['action']} by {latest['reviewer']}"

    if target_kind == "object":
        changed = gov_service.apply_object_state(
            db_path, target_id, prior_state, reviewer=reviewer, note=revert_note
        )
    else:
        changed = know_service.review_relationship(
            db_path, int(target_id), prior_state, reviewer=reviewer, note=revert_note
        )
    if not changed:
        return RevertResult(False, target_kind, target_id, reason="target not found")
    return RevertResult(
        True, target_kind, target_id, from_state=latest["action"], to_state=prior_state
    )


def revert_agent_actions(
    db_path: str | Path,
    *,
    agent: str | None = None,
    since: str | None = None,
    reviewer: str = "cli",
    note: str = "",
) -> RevertStats:
    """Roll back a batch of agent decisions, by agent name and/or time window.

    Only targets whose *latest* review action is still the agent's are reverted —
    if a human has since approved/rejected the item, it is left untouched, so the
    undo never overrides a human decision. ``agent=None`` matches every agent;
    ``since`` is an ISO-8601 timestamp lower bound.
    """

    prefix = agent_reviewer(agent) if agent else AGENT_PREFIX
    init_db(db_path)
    with connect(db_path) as conn:
        rows = know_repo.agent_reviews(conn, reviewer_prefix=prefix, since=since)

    stats = RevertStats()
    for row in rows:
        target_kind, target_id = row["target_kind"], str(row["target_id"])
        with connect(db_path) as conn:
            history = know_repo.reviews_for_target(conn, target_kind, target_id)
        latest = history[-1] if history else None
        # Skip if a human (or a different, later agent) holds the latest decision.
        if latest is None or not is_agent_reviewer(latest["reviewer"]):
            stats.skipped += 1
            continue
        if agent and latest["reviewer"] != prefix:
            stats.skipped += 1
            continue
        result = revert_review(
            db_path, target_kind, target_id, reviewer=reviewer, note=note
        )
        stats.results.append(result)
        if result.reverted:
            stats.reverted += 1
        else:
            stats.skipped += 1
    return stats


__all__ = [
    "AgentApprovalStats",
    "RevertResult",
    "RevertStats",
    "agent_approve",
    "object_eligible",
    "relationship_eligible",
    "revert_review",
    "revert_agent_actions",
]
