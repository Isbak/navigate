"""Integration tests for the knowledge consolidation pipeline.

These seed the semantic ``candidate_*`` tables directly (the input contract of
consolidation) and exercise the full pipeline: gathering, resolution, object
creation, evidence tracking, relationship creation, scoring, the review
workflow, status preservation across re-runs, and graph export.
"""

from catalog.db import connect, init_db
from catalog.knowledge import analytics
from catalog.knowledge import repository as repo
from catalog.knowledge.export import build_edges, build_nodes, export_graph_json
from catalog.knowledge.models import ReviewState
from catalog.knowledge.service import consolidate, review_object, review_relationship


def _seed_capability(conn, artifact_id, name, confidence=0.9, quote="quote"):
    conn.execute(
        """
        INSERT INTO candidate_capabilities(
            artifact_id, name, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, 'OBSERVATION', 'NEW', 'stub', 't')
        """,
        (artifact_id, name, confidence, quote),
    )


def _seed_entity(conn, artifact_id, entity_type, name, confidence=0.85, quote="quote"):
    conn.execute(
        """
        INSERT INTO candidate_entities(
            artifact_id, entity_type, name, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, 'OBSERVATION', 'NEW', 'stub', 't')
        """,
        (artifact_id, entity_type, name, confidence, quote),
    )


def _seed_relationship(conn, artifact_id, subject, predicate, obj, confidence=0.8):
    conn.execute(
        """
        INSERT INTO candidate_relationships(
            artifact_id, subject, predicate, object, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, ?, 'because', 'HYPOTHESIS', 'NEW', 'stub', 't')
        """,
        (artifact_id, subject, predicate, obj, confidence),
    )


def _seed_requirement(
    conn, artifact_id, standard_name, clause_ref, text, *, title="", confidence=0.9
):
    conn.execute(
        """
        INSERT INTO candidate_requirements(
            artifact_id, standard_name, standard_version, clause_ref, title,
            requirement_text, obligation_level, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, '2002', ?, ?, ?, 'MANDATORY', ?, ?, 'OBSERVATION',
                  'NEW', 'stub', 't')
        """,
        (artifact_id, standard_name, clause_ref, title, text, confidence, text),
    )


def _seed_decision(conn, artifact_id, decision_text, title, confidence=0.8):
    conn.execute(
        """
        INSERT INTO candidate_decisions(
            artifact_id, decision_text, title, confidence, supporting_text,
            knowledge_type, review_status, model, created_at
        ) VALUES (?, ?, ?, ?, 'quote', 'HYPOTHESIS', 'NEW', 'stub', 't')
        """,
        (artifact_id, decision_text, title, confidence),
    )


def _seed_release_governance(db):
    """Three docs naming Release Governance three ways, all on Salesforce."""

    init_db(db)
    with connect(db) as conn:
        _seed_capability(conn, "doc_a", "Release Governance", quote="we run release governance")
        _seed_capability(conn, "doc_b", "Release governance")
        _seed_capability(conn, "doc_c", "Release Governance Model")
        _seed_entity(conn, "doc_a", "Platform", "Salesforce")
        _seed_entity(conn, "doc_b", "Platform", "Salesforce")
        _seed_entity(conn, "doc_c", "Platform", "Salesforce")
        _seed_relationship(conn, "doc_a", "Salesforce", "implements", "Release Governance")
        conn.commit()


def test_variants_consolidate_into_one_object(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)

    stats = consolidate(db)
    assert stats.objects_created == 2  # one Capability, one Platform
    assert stats.mentions_linked == 6

    with connect(db) as conn:
        obj = repo.get_object(conn, "capability_release_governance")
        assert obj is not None
        assert obj["canonical_name"] == "Release Governance"
        assert obj["object_type"] == "Capability"
        assert obj["status"] == ReviewState.PROPOSED.value
        # Three documents mention it under three surface forms.
        mentions = repo.mentions_for_object(conn, "capability_release_governance")
        assert {m["artifact_id"] for m in mentions} == {"doc_a", "doc_b", "doc_c"}


def test_decisions_consolidate_by_title(tmp_path):
    # Two documents make the same decision with differently-worded full text but
    # the same short title; consolidation collapses them into one Decision object
    # (keyed on the title) instead of one node per sentence.
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed_decision(conn, "doc_a", "We will adopt the Launchpad model now",
                       "Adopt Launchpad model")
        _seed_decision(conn, "doc_b", "Adopt Launchpad as the operating model",
                       "Adopt Launchpad model")
        conn.commit()

    stats = consolidate(db)
    assert stats.objects_created == 1
    with connect(db) as conn:
        obj = repo.get_object(conn, "decision_adopt_launchpad_model")
        assert obj is not None
        assert obj["object_type"] == "Decision"
        mentions = repo.mentions_for_object(conn, "decision_adopt_launchpad_model")
        assert {m["artifact_id"] for m in mentions} == {"doc_a", "doc_b"}


def test_every_object_has_evidence(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)

    with connect(db) as conn:
        orphans = conn.execute(
            """
            SELECT o.id FROM knowledge_objects o
            LEFT JOIN knowledge_evidence e ON e.knowledge_object_id = o.id
            WHERE e.id IS NULL
            """
        ).fetchall()
    assert orphans == []


def _insert_artifact(conn, *, path, artifact_id, scan_status="UNCHANGED"):
    from pathlib import Path

    conn.execute(
        """
        INSERT INTO artifacts(
            path, id, filename, file_type, size_bytes, scan_status
        ) VALUES (?, ?, ?, 'txt', 1, ?)
        """,
        (str(path), artifact_id, Path(path).name, scan_status),
    )


def test_consolidation_scopes_to_configured_sources(tmp_path):
    """Only documents under a configured source folder are consolidated; curated
    imports always count; out-of-scope candidates persist and return on re-add."""

    db = tmp_path / "catalog.sqlite"
    keep = tmp_path / "keep"
    drop = tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    init_db(db)
    with connect(db) as conn:
        _insert_artifact(conn, path=keep / "a.txt", artifact_id="doc_keep")
        _insert_artifact(conn, path=drop / "b.txt", artifact_id="doc_drop")
        _seed_capability(conn, "doc_keep", "Kept Capability")
        _seed_capability(conn, "doc_drop", "Dropped Capability")
        _seed_capability(conn, "import_iso", "Curated Standard Thing")  # no artifact
        conn.commit()

    # Scope to ``keep`` only: drop's object is excluded, curated import survives.
    consolidate(db, source_paths=[str(keep)])
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_kept_capability") is not None
        assert repo.get_object(conn, "capability_dropped_capability") is None
        assert repo.get_object(conn, "capability_curated_standard_thing") is not None
        # The out-of-scope candidate row is untouched in the semantic layer.
        remaining = conn.execute(
            "SELECT COUNT(*) FROM candidate_capabilities WHERE artifact_id = 'doc_drop'"
        ).fetchone()[0]
        assert remaining == 1

    # Re-add ``drop`` to scope: its object returns (candidates were retained).
    consolidate(db, source_paths=[str(keep), str(drop)])
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_dropped_capability") is not None

    # source_paths=None disables scoping entirely (legacy behavior).
    consolidate(db, source_paths=None)
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_dropped_capability") is not None


def test_evidence_fallback_when_no_quotes(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed_capability(conn, "doc_a", "Quiet Capability", quote="")
        conn.commit()
    consolidate(db)

    with connect(db) as conn:
        ev = repo.evidence_for_object(conn, "capability_quiet_capability")
    assert len(ev) == 1
    # With no supporting quote, the name itself stands in as evidence.
    assert ev[0]["quote"] == "Quiet Capability"


def test_relationships_resolve_to_objects(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)

    with connect(db) as conn:
        rels = repo.all_relationships(conn)
    assert len(rels) == 1
    rel = rels[0]
    assert rel["source_object"] == "platform_salesforce"
    assert rel["predicate"] == "implements"
    assert rel["target_object"] == "capability_release_governance"
    assert rel["review_status"] == ReviewState.PROPOSED.value


def test_relationship_with_unresolvable_endpoint_is_skipped(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed_capability(conn, "doc_a", "Known Thing")
        _seed_relationship(conn, "doc_a", "Known Thing", "supports", "Nonexistent Mystery")
        conn.commit()
    stats = consolidate(db)
    assert stats.relationships_created == 0
    assert stats.relationships_unresolved == 1


def test_floating_object_is_connected_to_its_standard(tmp_path):
    # A concept extracted from a standard's document, with no mined relationship,
    # would float. The connectivity guarantee links it to the standard so the
    # graph has no islands - and a Requirement keeps its specific mandated_by edge
    # instead of a redundant appears_in.
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed_requirement(conn, "doc_a", "EN 1990", "6.2", "Verify the limit state.")
        _seed_entity(conn, "doc_a", "Concept", "Partial Factor")
        conn.commit()

    stats = consolidate(db)
    assert stats.relationships_structural >= 1
    assert stats.objects_floating == 0

    with connect(db) as conn:
        rels = repo.relationships_for_object(conn, "concept_partial_factor")
        appears = [r for r in rels if r["predicate"] == "appears_in"]
        assert len(appears) == 1
        assert appears[0]["source_object"] == "concept_partial_factor"
        assert appears[0]["target_object"] == "standard_en_1990"
        # Evidence is carried, keeping the structural edge traceable.
        assert appears[0]["evidence"] and appears[0]["evidence"] != "[]"

        # The requirement is connected by its specific mandated_by edge, not a
        # duplicate appears_in.
        req_rels = repo.relationships_for_object(conn, "requirement_en_1990_6_2")
        preds = {r["predicate"] for r in req_rels}
        assert "mandated_by" in preds
        assert "appears_in" not in preds


def test_clause_cross_reference_links_requirements(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed_requirement(
            conn, "doc_a", "EN 1990", "6.2",
            "Combinations shall be taken in accordance with clause 6.3.",
        )
        _seed_requirement(conn, "doc_a", "EN 1990", "6.3", "Partial factors apply.")
        conn.commit()

    stats = consolidate(db)
    assert stats.relationships_crossref >= 1

    with connect(db) as conn:
        rels = repo.relationships_for_object(conn, "requirement_en_1990_6_2")
        refs = [r for r in rels if r["predicate"] == "references"]
        assert any(r["target_object"] == "requirement_en_1990_6_3" for r in refs)


def test_standard_cross_reference_links_standards(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        _seed_requirement(
            conn, "doc_a", "EN 1992", "5.1",
            "Actions shall be determined in accordance with EN 1990.",
        )
        _seed_requirement(conn, "doc_b", "EN 1990", "1.1", "Basis of design.")
        conn.commit()

    consolidate(db)
    with connect(db) as conn:
        rels = repo.relationships_for_object(conn, "standard_en_1992")
        refs = [
            r for r in rels
            if r["predicate"] == "references"
            and r["target_object"] == "standard_en_1990"
        ]
        assert len(refs) == 1


def test_confidence_scales_with_document_support(tmp_path):
    db = tmp_path / "catalog.sqlite"
    init_db(db)
    with connect(db) as conn:
        for i in range(10):
            _seed_capability(conn, f"doc_{i}", "Widely Discussed Capability")
        _seed_capability(conn, "solo", "Rarely Mentioned Capability")
        conn.commit()
    consolidate(db)

    with connect(db) as conn:
        broad = repo.get_object(conn, "capability_widely_discussed_capability")
        narrow = repo.get_object(conn, "capability_rarely_mentioned_capability")
    assert broad["confidence"] > narrow["confidence"]


def test_review_workflow_and_status_preserved_across_reconsolidation(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)

    # Approve an object; default status is PROPOSED.
    assert review_object(db, "capability_release_governance", ReviewState.APPROVED.value)
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_release_governance")["status"] == "APPROVED"

    # A normal re-consolidate rebuilds derived data but keeps the approval.
    stats = consolidate(db)
    assert stats.statuses_preserved == 1
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_release_governance")["status"] == "APPROVED"

    # --force wipes the human decision back to PROPOSED.
    consolidate(db, force=True)
    with connect(db) as conn:
        assert repo.get_object(conn, "capability_release_governance")["status"] == "PROPOSED"


def test_reject_object_unknown_id_returns_false(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)
    assert review_object(db, "no_such_object", ReviewState.REJECTED.value) is False


def test_relationship_review_workflow_and_status_preserved(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)

    with connect(db) as conn:
        rel = repo.all_relationships(conn)[0]
        rel_id = rel["id"]
        assert rel["review_status"] == ReviewState.PROPOSED.value

    # Approve the relationship and record an audit-trail entry.
    assert review_relationship(db, rel_id, ReviewState.APPROVED.value)
    with connect(db) as conn:
        approved = repo.all_relationships(conn)[0]
        assert approved["review_status"] == ReviewState.APPROVED.value
        review = conn.execute(
            "SELECT * FROM knowledge_reviews WHERE target_kind = 'relationship'"
        ).fetchone()
        assert review["target_id"] == str(rel_id)
        assert review["action"] == ReviewState.APPROVED.value

    # A normal re-consolidate keeps the approval (keyed on the triple, not id).
    consolidate(db)
    with connect(db) as conn:
        assert repo.all_relationships(conn)[0]["review_status"] == ReviewState.APPROVED.value

    # --force discards the human decision back to PROPOSED.
    consolidate(db, force=True)
    with connect(db) as conn:
        assert repo.all_relationships(conn)[0]["review_status"] == ReviewState.PROPOSED.value


def test_review_relationship_unknown_id_returns_false(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)
    assert review_relationship(db, 999999, ReviewState.APPROVED.value) is False


def test_consolidate_is_idempotent(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)
    consolidate(db)
    with connect(db) as conn:
        assert repo.count_objects(conn) == 2
        assert repo.count_table(conn, "knowledge_relationships") == 1
        # Mentions are rebuilt, not duplicated.
        assert repo.count_table(conn, "knowledge_mentions") == 6


def test_analytics_answer_success_criteria(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)

    with connect(db) as conn:
        caps = analytics.top_by_type(conn, "Capability")
        assert caps[0]["name"] == "Release Governance"
        assert caps[0]["documents"] == 3

        mentioned = analytics.most_mentioned(conn)
        assert {m["name"] for m in mentioned} == {"Release Governance", "Salesforce"}

        connected = analytics.most_connected(conn)
        assert connected  # the implements relationship links two objects
        assert all(c["degree"] >= 1 for c in connected)


def test_graph_export_writes_nodes_and_edges(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)

    with connect(db) as conn:
        nodes = build_nodes(conn)
        edges = build_edges(conn)
    assert {n["id"] for n in nodes} == {
        "capability_release_governance",
        "platform_salesforce",
    }
    assert len(edges) == 1
    assert edges[0]["source"] == "platform_salesforce"
    assert edges[0]["target"] == "capability_release_governance"

    out = export_graph_json(connect(db), tmp_path / "graph")
    assert out["nodes"].exists()
    assert out["edges"].exists()


def test_rejected_object_excluded_from_graph(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_release_governance(db)
    consolidate(db)
    review_object(db, "platform_salesforce", ReviewState.REJECTED.value)

    with connect(db) as conn:
        nodes = build_nodes(conn)
        edges = build_edges(conn)
    ids = {n["id"] for n in nodes}
    assert "platform_salesforce" not in ids
    # The edge touching the rejected node is dropped too.
    assert edges == []
