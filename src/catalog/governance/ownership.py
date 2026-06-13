"""Knowledge ownership.

Every approved object should have an accountable owner - a Team, a Person, or a
Domain. Ownership is curated state that survives re-consolidation (it lives in
``knowledge_owners``, which references object ids softly), and every change is
recorded in the audit trail so the question "who owns this, and since when?" is
always answerable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..db import connect, init_db
from . import repository as repo
from .models import ChangeType, OwnerType

_VALID_OWNER_TYPES = {t.value for t in OwnerType}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def assign_owner(
    db_path: str | Path,
    object_id: str,
    owner_type: str,
    owner_id: str,
    *,
    assigned_by: str = "cli",
) -> bool:
    """Set an object's owner and log the change. Returns False if no such object.

    ``owner_type`` is normalized to one of Team / Person / Domain (case
    insensitive); anything else raises ``ValueError``.
    """

    canonical = {t.lower(): t for t in _VALID_OWNER_TYPES}.get(owner_type.lower())
    if canonical is None:
        raise ValueError(
            f"Unknown owner type {owner_type!r}; expected one of "
            f"{', '.join(sorted(_VALID_OWNER_TYPES))}"
        )

    init_db(db_path)
    now = _utc_now()
    with connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM knowledge_objects WHERE id = ?", (object_id,)
        ).fetchone()
        if exists is None:
            return False
        prior = repo.set_owner(
            conn,
            object_id=object_id,
            owner_type=canonical,
            owner_id=owner_id,
            assigned_at=now,
            assigned_by=assigned_by,
        )
        repo.insert_change(
            conn,
            change_type=ChangeType.OWNERSHIP_CHANGED.value,
            target_kind="object",
            object_id=object_id,
            field="owner",
            old_value=prior or "",
            new_value=f"{canonical}:{owner_id}",
            detail=f"owner set by {assigned_by}",
            detected_at=now,
        )
        conn.commit()
    return True


__all__ = ["assign_owner"]
