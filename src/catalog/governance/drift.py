"""Knowledge drift detection.

Drift is the slow divergence between what the organization *said* it knew and
what its documents *now* say. Detecting it is what keeps a governed graph
honest. This module compares a previous snapshot of the graph (captured from the
last governance scan) against the current one and surfaces four signals named in
the spec:

* **disappearing evidence** - an object whose document support collapsed.
* **removed knowledge**      - an established object that vanished entirely.
* **terminology change**     - an established object disappears in the same run
  that a new object of the same type appears, e.g. "Launchpad Model" (in 30
  documents) being replaced by "Mission Delivery Model".
* **relationship change**    - links that appeared or disappeared (passed in by
  the caller, which owns the relationship diff).

Everything here is a pure comparison of two plain dictionaries, so it is trivial
to test with hand-built before/after snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import DriftConfig


@dataclass(frozen=True)
class ObjectSnapshot:
    """The handful of fields drift detection needs about one object."""

    object_id: str
    name: str
    object_type: str
    document_count: int


@dataclass(frozen=True)
class DriftFinding:
    kind: str            # disappearing_evidence | removed | terminology_change
    object_id: str
    message: str
    related_id: str = ""


def detect_drift(
    previous: dict[str, ObjectSnapshot],
    current: dict[str, ObjectSnapshot],
    config: DriftConfig | None = None,
) -> list[DriftFinding]:
    """Compare two graph snapshots and return drift findings.

    ``previous`` and ``current`` map ``object_id -> ObjectSnapshot``. The
    function is order-independent and never mutates its inputs.
    """

    config = config or DriftConfig()
    findings: list[DriftFinding] = []

    removed_ids = [oid for oid in previous if oid not in current]
    added_ids = [oid for oid in current if oid not in previous]

    # Established objects that fully disappeared.
    established_removed = [
        previous[oid]
        for oid in removed_ids
        if previous[oid].document_count >= config.terminology_min_documents
    ]
    for snap in established_removed:
        findings.append(
            DriftFinding(
                kind="removed",
                object_id=snap.object_id,
                message=(
                    f"{snap.name} disappeared from the corpus "
                    f"(was in {snap.document_count} documents)"
                ),
            )
        )

    # Terminology change: an established object vanishes while a new object of
    # the same type appears in the same run - the classic rename/rebrand.
    added_by_type: dict[str, list[ObjectSnapshot]] = {}
    for oid in added_ids:
        snap = current[oid]
        added_by_type.setdefault(snap.object_type, []).append(snap)
    for snap in established_removed:
        replacements = added_by_type.get(snap.object_type, [])
        for repl in replacements:
            findings.append(
                DriftFinding(
                    kind="terminology_change",
                    object_id=snap.object_id,
                    related_id=repl.object_id,
                    message=(
                        f"{snap.name} ({snap.document_count} documents) may have "
                        f"been replaced by {repl.name}"
                    ),
                )
            )

    # Disappearing evidence: an object that survives but whose document support
    # collapsed to (at most) a fraction of what it had.
    for oid, cur in current.items():
        prev = previous.get(oid)
        if prev is None or prev.document_count == 0:
            continue
        ratio = cur.document_count / prev.document_count
        if cur.document_count < prev.document_count and ratio <= config.evidence_drop_ratio:
            findings.append(
                DriftFinding(
                    kind="disappearing_evidence",
                    object_id=oid,
                    message=(
                        f"{cur.name} evidence dropped from {prev.document_count} "
                        f"to {cur.document_count} documents"
                    ),
                )
            )

    return findings


__all__ = ["ObjectSnapshot", "DriftFinding", "detect_drift"]
