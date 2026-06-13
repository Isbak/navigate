"""Tests for the RDF projection and Fuseki integration (Prompt #7).

These seed the semantic ``candidate_*`` tables, consolidate into knowledge
objects, approve a subset, and assert the RDF export only ever projects APPROVED
data. The Fuseki upload is exercised with a recording stub so no network or
running server is required.
"""

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from catalog.db import connect, init_db
from catalog.knowledge.models import ReviewState
from catalog.knowledge.service import consolidate, review_object
from catalog.rdf.config import FusekiConfig, load_jena_config
from catalog.rdf.export import (
    build_knowledge_graph,
    build_provenance_graph,
    build_relationships_graph,
    export_rdf,
    rdf_stats,
    validate_rdf,
)
from catalog.rdf.fuseki import FusekiError, clear_dataset, fuseki_load
from catalog.rdf.namespaces import (
    KG,
    class_uri,
    evidence_uri,
    object_uri,
    predicate_uri,
)
from catalog.rdf.ontology import build_ontology_graph


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


def _seed_and_consolidate(db):
    """Salesforce implements Release Governance, mentioned across three docs."""

    init_db(db)
    with connect(db) as conn:
        _seed_capability(conn, "doc_a", "Release Governance", quote="we run release governance")
        _seed_capability(conn, "doc_b", "Release governance")
        _seed_capability(conn, "doc_c", "Release Governance Model")
        _seed_entity(conn, "doc_a", "Platform", "Salesforce")
        _seed_entity(conn, "doc_b", "Platform", "Salesforce")
        _seed_relationship(conn, "doc_a", "Salesforce", "implements", "Release Governance")
        conn.commit()
    consolidate(db)


def _approve_all(db):
    with connect(db) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM knowledge_objects")]
    for oid in ids:
        review_object(db, oid, ReviewState.APPROVED.value)


# -- URI generation -----------------------------------------------------------

def test_object_uri_is_path_style_and_stable():
    uri = object_uri("Capability", "capability_release_governance")
    assert uri == URIRef("https://knowledge-atlas.local/kg/capability/release_governance")


def test_object_uri_preserves_collision_suffix():
    uri = object_uri("Capability", "capability_release_governance_2")
    assert str(uri).endswith("/capability/release_governance_2")


def test_class_and_predicate_uris():
    assert class_uri("Capability") == URIRef("https://knowledge-atlas.local/kg/Capability")
    # snake_case predicates map to the camelCase ontology predicate.
    assert predicate_uri("depends_on") == URIRef("https://knowledge-atlas.local/kg/dependsOn")
    assert predicate_uri("supports") == URIRef("https://knowledge-atlas.local/kg/supports")


def test_evidence_uri():
    assert evidence_uri(123) == URIRef("https://knowledge-atlas.local/kg/evidence/123")


# -- ontology export ----------------------------------------------------------

def test_ontology_declares_all_classes_and_predicates():
    g = build_ontology_graph()
    for object_type in (
        "Capability", "Initiative", "Technology", "Platform", "Team",
        "Product", "Concept", "Decision", "Risk", "Process",
    ):
        assert (KG[object_type], RDF.type, RDFS.Class) in g
    for predicate in (
        "supports", "dependsOn", "implements", "affects",
        "relatedTo", "ownedBy", "mentions", "references",
    ):
        assert (KG[predicate], RDF.type, RDF.Property) in g
    # Provenance vocabulary is present too.
    assert (KG["Evidence"], RDF.type, RDFS.Class) in g
    assert (KG["supportedBy"], RDF.type, RDF.Property) in g


# -- object export ------------------------------------------------------------

def test_only_approved_objects_are_exported(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)

    # Nothing approved yet -> empty knowledge graph.
    with connect(db) as conn:
        assert len(build_knowledge_graph(conn)) == 0

    # Approve just the capability.
    review_object(db, "capability_release_governance", ReviewState.APPROVED.value)
    with connect(db) as conn:
        g = build_knowledge_graph(conn)
    cap = object_uri("Capability", "capability_release_governance")
    assert (cap, RDF.type, KG["Capability"]) in g
    assert (cap, RDFS.label, Literal("Release Governance")) in g
    # The unapproved platform must not appear.
    plat = object_uri("Platform", "platform_salesforce")
    assert (plat, RDF.type, KG["Platform"]) not in g


def test_object_carries_confidence(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)
    _approve_all(db)
    with connect(db) as conn:
        g = build_knowledge_graph(conn)
    cap = object_uri("Capability", "capability_release_governance")
    confidences = list(g.objects(cap, KG["confidence"]))
    assert len(confidences) == 1
    assert 0.0 <= float(confidences[0]) <= 1.0


# -- relationship export ------------------------------------------------------

def test_relationship_export_requires_both_endpoints_approved(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)

    # Approve only the capability; the relationship's source (platform) is not.
    review_object(db, "capability_release_governance", ReviewState.APPROVED.value)
    with connect(db) as conn:
        assert len(build_relationships_graph(conn)) == 0

    # Approve the platform too -> the implements triple is projected.
    review_object(db, "platform_salesforce", ReviewState.APPROVED.value)
    # The relationship itself must be approved as well.
    with connect(db) as conn:
        rel_id = conn.execute(
            "SELECT id FROM knowledge_relationships LIMIT 1"
        ).fetchone()["id"]
        conn.execute(
            "UPDATE knowledge_relationships SET review_status = ? WHERE id = ?",
            (ReviewState.APPROVED.value, rel_id),
        )
        conn.commit()

    with connect(db) as conn:
        g = build_relationships_graph(conn)
    triple = (
        object_uri("Platform", "platform_salesforce"),
        predicate_uri("implements"),
        object_uri("Capability", "capability_release_governance"),
    )
    assert triple in g


# -- provenance export --------------------------------------------------------

def test_provenance_links_objects_to_evidence(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)
    _approve_all(db)
    with connect(db) as conn:
        g = build_provenance_graph(conn)
        cap = object_uri("Capability", "capability_release_governance")
        # The capability is supportedBy at least one evidence resource.
        evidence_nodes = list(g.objects(cap, KG["supportedBy"]))
    assert evidence_nodes
    ev = evidence_nodes[0]
    assert (ev, RDF.type, KG["Evidence"]) in g
    assert list(g.objects(ev, KG["sourceArtifact"]))
    assert list(g.objects(ev, KG["quote"]))


# -- RDF validation -----------------------------------------------------------

def test_export_writes_four_valid_turtle_files(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)
    _approve_all(db)
    out = tmp_path / "rdf"
    with connect(db) as conn:
        paths = export_rdf(conn, out)
    for name in ("ontology", "knowledge", "relationships", "provenance"):
        assert paths[name].exists()
        assert paths[name].suffix == ".ttl"

    results = validate_rdf(out)
    assert set(results) == {
        "ontology.ttl", "knowledge.ttl", "relationships.ttl", "provenance.ttl"
    }
    assert all(r["ok"] for r in results.values())
    # The exported knowledge re-parses and contains the capability.
    g = Graph()
    g.parse(str(paths["knowledge"]))
    assert (
        object_uri("Capability", "capability_release_governance"),
        RDF.type,
        KG["Capability"],
    ) in g


def test_export_supports_jsonld_and_ntriples(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)
    _approve_all(db)
    for fmt, ext in (("json-ld", "jsonld"), ("nt", "nt")):
        out = tmp_path / fmt
        with connect(db) as conn:
            paths = export_rdf(conn, out, fmt=fmt)
        assert paths["knowledge"].suffix == f".{ext}"
        g = Graph()
        g.parse(str(paths["knowledge"]))
        assert len(g) > 0


def test_rdf_stats_counts_only_approved(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)
    with connect(db) as conn:
        assert rdf_stats(conn)["objects"] == 0
    _approve_all(db)
    with connect(db) as conn:
        stats = rdf_stats(conn)
    assert stats["objects"] == 2
    assert stats["evidence"] >= 1


# -- Fuseki upload ------------------------------------------------------------

class _RecordingPoster:
    """Captures (url, body, content_type) instead of hitting the network."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body, content_type):
        self.calls.append((url, body.decode("utf-8"), content_type))


def test_fuseki_load_uploads_in_order(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed_and_consolidate(db)
    _approve_all(db)
    out = tmp_path / "rdf"
    with connect(db) as conn:
        export_rdf(conn, out)

    poster = _RecordingPoster()
    config = FusekiConfig(endpoint="http://fuseki.test/knowledge-atlas")
    uploaded = fuseki_load(config, out, poster=poster)

    # Ontology and knowledge always have triples; every call is INSERT DATA to /update.
    assert uploaded["ontology"] > 0
    assert uploaded["knowledge"] > 0
    for url, body, content_type in poster.calls:
        assert url == "http://fuseki.test/knowledge-atlas/update"
        assert body.startswith("INSERT DATA {")
        assert content_type == "application/sparql-update"


def test_fuseki_load_fails_when_not_exported(tmp_path):
    config = FusekiConfig()
    poster = _RecordingPoster()
    try:
        fuseki_load(config, tmp_path / "missing", poster=poster)
    except FusekiError as exc:
        assert "rdf-export" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FusekiError")
    assert poster.calls == []


def test_fuseki_clear_sends_clear_all():
    poster = _RecordingPoster()
    config = FusekiConfig(endpoint="http://fuseki.test/knowledge-atlas")
    clear_dataset(config, poster=poster)
    assert poster.calls == [
        ("http://fuseki.test/knowledge-atlas/update", "CLEAR ALL", "application/sparql-update")
    ]


# -- config -------------------------------------------------------------------

def test_load_jena_config_defaults_when_missing(tmp_path):
    config = load_jena_config(tmp_path / "nope.yml")
    assert config.endpoint == "http://localhost:3030/knowledge-atlas"
    assert config.dataset == "atlas"
    assert config.update_url == "http://localhost:3030/knowledge-atlas/update"


def test_load_jena_config_reads_file(tmp_path):
    cfg = tmp_path / "jena.yml"
    cfg.write_text(
        "fuseki:\n  endpoint: http://example.org/ds\n  dataset: mine\n",
        encoding="utf-8",
    )
    config = load_jena_config(cfg)
    assert config.endpoint == "http://example.org/ds"
    assert config.dataset == "mine"
