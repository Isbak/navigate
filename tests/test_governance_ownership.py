"""Tests for governance ownership (Prompt #10)."""

import pytest

from catalog.db import connect
from catalog.governance import repository as repo
from catalog.governance.ownership import assign_owner
from catalog.knowledge.service import consolidate


def test_assign_owner_persists(governed_db):
    assert assign_owner(governed_db, "capability_release_governance", "Team", "Test & Release Team")
    with connect(governed_db) as conn:
        owner = repo.get_owner(conn, "capability_release_governance")
    assert owner["owner_type"] == "Team"
    assert owner["owner_id"] == "Test & Release Team"


def test_owner_type_is_case_insensitive(governed_db):
    assert assign_owner(governed_db, "capability_release_governance", "team", "X")
    with connect(governed_db) as conn:
        owner = repo.get_owner(conn, "capability_release_governance")
    assert owner["owner_type"] == "Team"


def test_unknown_owner_type_rejected(governed_db):
    with pytest.raises(ValueError):
        assign_owner(governed_db, "capability_release_governance", "Robot", "X")


def test_assign_owner_unknown_object(governed_db):
    assert assign_owner(governed_db, "capability_does_not_exist", "Team", "X") is False


def test_reassigning_owner_overwrites_and_logs(governed_db):
    assign_owner(governed_db, "capability_release_governance", "Team", "First Team")
    assign_owner(governed_db, "capability_release_governance", "Domain", "Test & Release")
    with connect(governed_db) as conn:
        owner = repo.get_owner(conn, "capability_release_governance")
        changes = repo.changes_for_object(conn, "capability_release_governance")
    assert owner["owner_type"] == "Domain"
    ownership_changes = [c for c in changes if c["change_type"] == "ownership_changed"]
    assert len(ownership_changes) == 2
    assert ownership_changes[1]["old_value"] == "Team:First Team"


def test_ownership_survives_reconsolidation(governed_db):
    # Ownership is curated state: a re-consolidate (which deletes and recreates
    # knowledge_objects) must not wipe it.
    assign_owner(governed_db, "capability_release_governance", "Team", "Test & Release Team")
    consolidate(governed_db)
    with connect(governed_db) as conn:
        owner = repo.get_owner(conn, "capability_release_governance")
    assert owner is not None
    assert owner["owner_id"] == "Test & Release Team"
