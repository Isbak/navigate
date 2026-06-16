from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/catalog.sqlite")

# ``path`` is the natural identity of a file location, so it is the primary key.
# ``id`` is content-addressed (doc_<first 12 sha256 chars>) and is therefore the
# SAME for byte-identical files: duplicates share an id, which is exactly how we
# detect them. ``id`` is indexed but intentionally not UNIQUE.
SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS artifacts(
  path TEXT PRIMARY KEY,
  id TEXT NOT NULL,
  filename TEXT NOT NULL,
  file_type TEXT NOT NULL,
  size_bytes INTEGER,
  created_at TEXT,
  modified_at TEXT,
  sha256 TEXT,
  source_system TEXT DEFAULT 'local_laptop',
  scan_status TEXT DEFAULT 'RAW',
  first_seen_at TEXT,
  last_scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_sha256 ON artifacts(sha256);
CREATE INDEX IF NOT EXISTS idx_artifacts_id ON artifacts(id);
CREATE INDEX IF NOT EXISTS idx_artifacts_status ON artifacts(scan_status);
-- Discovered hyperlinks, normalized and classified by the link discovery layer.
--
-- NOTE on ``source_artifact_id``: it references the content-addressed artifact
-- id (``doc_<sha>``). That column is intentionally NOT unique in ``artifacts``
-- (byte-identical duplicates share an id, which is how duplicates are detected),
-- and SQLite can only enforce a FOREIGN KEY against a UNIQUE/PRIMARY KEY parent.
-- We therefore model the relationship with an index rather than an enforced
-- constraint; integrity is maintained by the discovery service.
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_artifact_id TEXT NOT NULL,
  raw_url TEXT NOT NULL,
  normalized_url TEXT NOT NULL,
  anchor_text TEXT,
  target_system TEXT,
  target_type TEXT,
  link_kind TEXT,
  discovered_at TEXT,
  last_seen_at TEXT,
  status TEXT DEFAULT 'ACTIVE'
);
CREATE INDEX IF NOT EXISTS idx_links_artifact ON links(source_artifact_id);
CREATE INDEX IF NOT EXISTS idx_links_normalized ON links(normalized_url);
CREATE INDEX IF NOT EXISTS idx_links_system ON links(target_system);
-- Deduplication key: source_artifact_id + normalized_url + anchor_text.
-- COALESCE keeps NULL and missing anchor text from being treated as distinct.
CREATE UNIQUE INDEX IF NOT EXISTS idx_links_dedup
  ON links(source_artifact_id, normalized_url, COALESCE(anchor_text, ''));
CREATE TABLE IF NOT EXISTS link_scan_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT,
  completed_at TEXT,
  artifacts_processed INTEGER,
  links_found INTEGER,
  links_new INTEGER,
  links_updated INTEGER,
  links_removed INTEGER,
  errors INTEGER
);
CREATE TABLE IF NOT EXISTS scan_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT,
  finished_at TEXT,
  files_scanned INTEGER DEFAULT 0,
  new_files INTEGER DEFAULT 0,
  changed_files INTEGER DEFAULT 0,
  unchanged_files INTEGER DEFAULT 0,
  duplicate_files INTEGER DEFAULT 0,
  deleted_files INTEGER DEFAULT 0
);
-- ---------------------------------------------------------------------------
-- Semantic classification layer (Prompt #5).
--
-- These tables hold what an LLM *proposes* about each document: classifications,
-- observations, hypotheses, and candidate relationships. Nothing here is a fact.
-- Every row carries provenance (artifact_id, model, created_at), a confidence in
-- [0.0, 1.0], the supporting_text it was derived from, a knowledge_type
-- (OBSERVATION or HYPOTHESIS - never FACT in this phase), and a review_status
-- that starts at NEW for a human to approve later. All of these are fully
-- regenerable from the cache via ``catalog classify``.
--
-- ``artifact_id`` references the content-addressed artifact id (``doc_<sha>``),
-- which is intentionally not UNIQUE in ``artifacts`` (duplicates share an id), so
-- the relationship is modeled with an index rather than an enforced FK - the same
-- approach used by the ``links`` table.
CREATE TABLE IF NOT EXISTS document_classifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  document_type TEXT,
  type_confidence REAL,
  domains TEXT,
  short_summary TEXT,
  long_summary TEXT,
  knowledge_type TEXT DEFAULT 'OBSERVATION',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  source_hash TEXT,
  created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_classifications_artifact
  ON document_classifications(artifact_id);
CREATE TABLE IF NOT EXISTS candidate_entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  entity_type TEXT,
  name TEXT,
  confidence REAL,
  supporting_text TEXT,
  knowledge_type TEXT DEFAULT 'OBSERVATION',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_entities_artifact ON candidate_entities(artifact_id);
CREATE INDEX IF NOT EXISTS idx_candidate_entities_type ON candidate_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_candidate_entities_name ON candidate_entities(name);
CREATE TABLE IF NOT EXISTS candidate_capabilities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  name TEXT,
  confidence REAL,
  supporting_text TEXT,
  knowledge_type TEXT DEFAULT 'OBSERVATION',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_capabilities_artifact ON candidate_capabilities(artifact_id);
CREATE INDEX IF NOT EXISTS idx_candidate_capabilities_name ON candidate_capabilities(name);
CREATE TABLE IF NOT EXISTS candidate_decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  decision_text TEXT,
  confidence REAL,
  supporting_text TEXT,
  knowledge_type TEXT DEFAULT 'HYPOTHESIS',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_decisions_artifact ON candidate_decisions(artifact_id);
CREATE TABLE IF NOT EXISTS candidate_risks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  risk_description TEXT,
  confidence REAL,
  supporting_text TEXT,
  knowledge_type TEXT DEFAULT 'HYPOTHESIS',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_risks_artifact ON candidate_risks(artifact_id);
CREATE TABLE IF NOT EXISTS candidate_relationships(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  subject TEXT,
  predicate TEXT,
  object TEXT,
  confidence REAL,
  supporting_text TEXT,
  knowledge_type TEXT DEFAULT 'HYPOTHESIS',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_relationships_artifact ON candidate_relationships(artifact_id);
-- Normative clauses mined from documents classified as a standard/regulation, or
-- loaded from a curated framework catalog (``catalog compliance import``). Each
-- row becomes a Requirement (and its parent Standard) knowledge object during
-- consolidation. Curated rows carry ``model='curated_import'``.
CREATE TABLE IF NOT EXISTS candidate_requirements(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_id TEXT NOT NULL,
  standard_name TEXT,
  standard_version TEXT,
  clause_ref TEXT,
  title TEXT,
  requirement_text TEXT,
  obligation_level TEXT DEFAULT 'MANDATORY',
  confidence REAL,
  supporting_text TEXT,
  knowledge_type TEXT DEFAULT 'OBSERVATION',
  review_status TEXT DEFAULT 'NEW',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_requirements_artifact ON candidate_requirements(artifact_id);
CREATE INDEX IF NOT EXISTS idx_candidate_requirements_standard ON candidate_requirements(standard_name);
CREATE TABLE IF NOT EXISTS classification_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT,
  completed_at TEXT,
  model TEXT,
  documents_processed INTEGER,
  documents_skipped INTEGER,
  errors INTEGER
);
-- ---------------------------------------------------------------------------
-- Knowledge consolidation layer (Prompt #6).
--
-- These tables converge the per-document semantic proposals into reusable
-- knowledge objects. Unlike the semantic ``artifact_id`` columns (which point at
-- the non-unique content id), a ``knowledge_object_id`` references
-- ``knowledge_objects.id`` - a real, stable, URI-ready primary key - so genuine
-- FOREIGN KEYs with ON DELETE CASCADE are used here.
--
-- ``knowledge_objects.id`` is a slug of the form ``<type>_<name>`` (e.g.
-- ``capability_release_governance``); it is stable across consolidation runs,
-- which is what lets a re-run preserve human review decisions and what a future
-- RDF mapping will adopt as the resource identifier. Everything here is fully
-- regenerable from the semantic tables via ``catalog consolidate``.
CREATE TABLE IF NOT EXISTS knowledge_objects(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  object_type TEXT NOT NULL,
  description TEXT,
  canonical_name TEXT,
  confidence REAL,
  status TEXT DEFAULT 'PROPOSED',
  merge_confidence REAL,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_objects_type ON knowledge_objects(object_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_objects_status ON knowledge_objects(status);
CREATE TABLE IF NOT EXISTS knowledge_mentions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  knowledge_object_id TEXT NOT NULL REFERENCES knowledge_objects(id) ON DELETE CASCADE,
  artifact_id TEXT NOT NULL,
  confidence REAL,
  source_text TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_mentions_object ON knowledge_mentions(knowledge_object_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_mentions_artifact ON knowledge_mentions(artifact_id);
CREATE TABLE IF NOT EXISTS knowledge_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  knowledge_object_id TEXT NOT NULL REFERENCES knowledge_objects(id) ON DELETE CASCADE,
  artifact_id TEXT NOT NULL,
  quote TEXT,
  page_number INTEGER,
  slide_number INTEGER,
  clause_ref TEXT,
  confidence REAL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_object ON knowledge_evidence(knowledge_object_id);
CREATE TABLE IF NOT EXISTS knowledge_relationships(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_object TEXT NOT NULL REFERENCES knowledge_objects(id) ON DELETE CASCADE,
  predicate TEXT NOT NULL,
  target_object TEXT NOT NULL REFERENCES knowledge_objects(id) ON DELETE CASCADE,
  confidence REAL,
  evidence TEXT,
  review_status TEXT DEFAULT 'PROPOSED',
  created_at TEXT,
  updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_rel_triple
  ON knowledge_relationships(source_object, predicate, target_object);
CREATE INDEX IF NOT EXISTS idx_knowledge_rel_source ON knowledge_relationships(source_object);
CREATE INDEX IF NOT EXISTS idx_knowledge_rel_target ON knowledge_relationships(target_object);
CREATE TABLE IF NOT EXISTS knowledge_reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  action TEXT NOT NULL,
  confidence REAL,
  note TEXT,
  reviewer TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_target ON knowledge_reviews(target_id);
-- ---------------------------------------------------------------------------
-- Knowledge governance layer (Prompt #10).
--
-- These tables turn the consolidated graph into a *governed* knowledge system:
-- ownership, a freshness lifecycle, quality scores, alerts, and a full change
-- audit trail. Unlike the consolidation tables, they hold *curated* state that
-- must survive a ``consolidate`` (which deletes and recreates knowledge_objects):
-- ownership, review decisions, and freshness history would be destroyed by an
-- ON DELETE CASCADE. They therefore reference ``knowledge_objects.id`` softly
-- (by value, with an index) rather than via an enforced foreign key - the same
-- approach the links/semantic tables use for the non-unique artifact id - so the
-- governance history is preserved across re-consolidation.
CREATE TABLE IF NOT EXISTS knowledge_owners(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id TEXT NOT NULL,
  owner_type TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  assigned_at TEXT,
  assigned_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_owners_object ON knowledge_owners(object_id);
CREATE TABLE IF NOT EXISTS knowledge_lifecycle(
  object_id TEXT PRIMARY KEY,
  name TEXT,
  object_type TEXT,
  created_at TEXT,
  last_seen_at TEXT,
  last_reviewed_at TEXT,
  last_confirmed_at TEXT,
  last_confidence REAL,
  freshness_score REAL,
  freshness_state TEXT DEFAULT 'FRESH',
  review_state TEXT DEFAULT 'PENDING_REVIEW',
  present INTEGER DEFAULT 1,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_freshness ON knowledge_lifecycle(freshness_state);
CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_review ON knowledge_lifecycle(review_state);
CREATE TABLE IF NOT EXISTS knowledge_quality(
  object_id TEXT PRIMARY KEY,
  quality_score REAL,
  evidence_score REAL,
  review_score REAL,
  freshness_score REAL,
  consistency_score REAL,
  owner_score REAL,
  confidence_score REAL,
  evidence_count INTEGER,
  document_count INTEGER,
  computed_at TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_type TEXT NOT NULL,
  severity TEXT DEFAULT 'INFO',
  object_id TEXT,
  message TEXT,
  status TEXT DEFAULT 'OPEN',
  created_at TEXT,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_alerts_type ON knowledge_alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_alerts_object ON knowledge_alerts(object_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_alerts_status ON knowledge_alerts(status);
CREATE TABLE IF NOT EXISTS knowledge_change_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  change_type TEXT NOT NULL,
  target_kind TEXT,
  object_id TEXT,
  field TEXT,
  old_value TEXT,
  new_value TEXT,
  detail TEXT,
  detected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_change_log_object ON knowledge_change_log(object_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_change_log_type ON knowledge_change_log(change_type);
-- ---------------------------------------------------------------------------
-- Compliance & standards layer.
--
-- These tables enrich the Standard/Requirement knowledge objects with what the
-- generic object model cannot carry (clause locators, versions, effective dates)
-- and hold the human-curated compliance assessment record and its evidence.
-- Like the governance tables, they hold curated state that must survive a
-- ``consolidate`` (which deletes and recreates knowledge_objects): assessments,
-- sign-offs, and their evidence would be destroyed by an ON DELETE CASCADE. They
-- therefore reference ``knowledge_objects.id`` softly (by value, with an index)
-- rather than via an enforced foreign key, so the compliance history is
-- preserved across re-consolidation.
CREATE TABLE IF NOT EXISTS compliance_standards(
  object_id TEXT PRIMARY KEY,
  name TEXT,
  authority TEXT,
  version TEXT,
  jurisdiction TEXT,
  effective_from TEXT,
  source_url TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS compliance_requirements(
  object_id TEXT PRIMARY KEY,
  standard_object_id TEXT,
  clause_ref TEXT,
  title TEXT,
  requirement_text TEXT,
  obligation_level TEXT DEFAULT 'MANDATORY',
  assessed_against_version TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_compliance_requirements_standard ON compliance_requirements(standard_object_id);
CREATE TABLE IF NOT EXISTS compliance_assessments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requirement_object_id TEXT NOT NULL,
  control_object_id TEXT,
  status TEXT NOT NULL DEFAULT 'UNASSESSED',
  assessed_against_version TEXT,
  rationale TEXT,
  assessor TEXT,
  assessed_at TEXT,
  review_status TEXT DEFAULT 'PROPOSED',
  created_at TEXT,
  updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compliance_assessments_pair
  ON compliance_assessments(requirement_object_id, COALESCE(control_object_id, ''));
CREATE INDEX IF NOT EXISTS idx_compliance_assessments_req ON compliance_assessments(requirement_object_id);
CREATE INDEX IF NOT EXISTS idx_compliance_assessments_status ON compliance_assessments(status);
CREATE TABLE IF NOT EXISTS compliance_assessment_evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assessment_id INTEGER NOT NULL REFERENCES compliance_assessments(id) ON DELETE CASCADE,
  artifact_id TEXT,
  quote TEXT,
  clause_ref TEXT,
  page_number INTEGER,
  confidence REAL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_compliance_assessment_evidence_assessment
  ON compliance_assessment_evidence(assessment_id);
CREATE TABLE IF NOT EXISTS compliance_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT,
  finished_at TEXT,
  requirements_assessed INTEGER DEFAULT 0,
  satisfied INTEGER DEFAULT 0,
  partial INTEGER DEFAULT 0,
  gaps INTEGER DEFAULT 0,
  not_applicable INTEGER DEFAULT 0,
  coverage REAL DEFAULT 0.0,
  errors INTEGER DEFAULT 0
);
-- ---------------------------------------------------------------------------
-- API job tracking (REST API layer).
--
-- The REST API can trigger the long-running pipeline operations (scan, extract,
-- discover-links, classify, consolidate) on demand. Each invocation is recorded
-- here so a client can poll its status and read a result summary afterwards. The
-- table is independent of the regenerable knowledge tables and is never dropped
-- by the rebuild logic above, so the job history survives a re-scan.
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  started_at TEXT,
  completed_at TEXT,
  error_message TEXT,
  result_summary TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

# Columns expected on a current ``artifacts`` table; a mismatch triggers a
# rebuild of the (regenerable) local index.
_EXPECTED_ARTIFACT_COLUMNS = {
    "path",
    "id",
    "filename",
    "file_type",
    "size_bytes",
    "created_at",
    "modified_at",
    "sha256",
    "source_system",
    "scan_status",
    "first_seen_at",
    "last_scanned_at",
}

# Columns expected on a current ``links`` table. The link schema evolved (it now
# stores normalized/classified links keyed by artifact id), so an older layout is
# dropped and recreated rather than migrated in place.
_EXPECTED_LINK_COLUMNS = {
    "id",
    "source_artifact_id",
    "raw_url",
    "normalized_url",
    "anchor_text",
    "target_system",
    "target_type",
    "link_kind",
    "discovered_at",
    "last_seen_at",
    "status",
}


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _columns_mismatch(conn: sqlite3.Connection, table: str, expected: set[str]) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        return False  # fresh database; CREATE statements handle it
    columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    return columns != expected


def _needs_rebuild(conn: sqlite3.Connection) -> bool:
    return _columns_mismatch(conn, "artifacts", _EXPECTED_ARTIFACT_COLUMNS)


def _needs_links_rebuild(conn: sqlite3.Connection) -> bool:
    return _columns_mismatch(conn, "links", _EXPECTED_LINK_COLUMNS)


# Expected columns for the semantic-layer tables. They are fully regenerable
# from the cache via ``catalog classify``, so a layout change drops and recreates
# the affected table rather than migrating it in place.
_EXPECTED_SEMANTIC_COLUMNS = {
    "document_classifications": {
        "id", "artifact_id", "document_type", "type_confidence", "domains",
        "short_summary", "long_summary", "knowledge_type", "review_status",
        "model", "source_hash", "created_at",
    },
    "candidate_entities": {
        "id", "artifact_id", "entity_type", "name", "confidence",
        "supporting_text", "knowledge_type", "review_status", "model", "created_at",
    },
    "candidate_capabilities": {
        "id", "artifact_id", "name", "confidence", "supporting_text",
        "knowledge_type", "review_status", "model", "created_at",
    },
    "candidate_decisions": {
        "id", "artifact_id", "decision_text", "confidence", "supporting_text",
        "knowledge_type", "review_status", "model", "created_at",
    },
    "candidate_risks": {
        "id", "artifact_id", "risk_description", "confidence", "supporting_text",
        "knowledge_type", "review_status", "model", "created_at",
    },
    "candidate_relationships": {
        "id", "artifact_id", "subject", "predicate", "object", "confidence",
        "supporting_text", "knowledge_type", "review_status", "model", "created_at",
    },
    "candidate_requirements": {
        "id", "artifact_id", "standard_name", "standard_version", "clause_ref",
        "title", "requirement_text", "obligation_level", "confidence",
        "supporting_text", "knowledge_type", "review_status", "model", "created_at",
    },
    "classification_runs": {
        "id", "started_at", "completed_at", "model",
        "documents_processed", "documents_skipped", "errors",
    },
}


def _stale_semantic_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        table
        for table, expected in _EXPECTED_SEMANTIC_COLUMNS.items()
        if _columns_mismatch(conn, table, expected)
    ]


# Expected columns for the knowledge-layer tables. Like the semantic tables they
# are fully regenerable (via ``catalog consolidate``), so a layout change drops
# and recreates them. They are dropped as a set, children first, because of the
# FOREIGN KEYs between them.
_EXPECTED_KNOWLEDGE_COLUMNS = {
    "knowledge_objects": {
        "id", "name", "object_type", "description", "canonical_name",
        "confidence", "status", "merge_confidence", "created_at", "updated_at",
    },
    "knowledge_mentions": {
        "id", "knowledge_object_id", "artifact_id", "confidence",
        "source_text", "created_at",
    },
    "knowledge_evidence": {
        "id", "knowledge_object_id", "artifact_id", "quote",
        "page_number", "slide_number", "clause_ref", "confidence", "created_at",
    },
    "knowledge_relationships": {
        "id", "source_object", "predicate", "target_object", "confidence",
        "evidence", "review_status", "created_at", "updated_at",
    },
    "knowledge_reviews": {
        "id", "target_kind", "target_id", "action", "confidence",
        "note", "reviewer", "created_at",
    },
}

# Drop order: children before the parent ``knowledge_objects`` they reference.
_KNOWLEDGE_DROP_ORDER = (
    "knowledge_relationships",
    "knowledge_evidence",
    "knowledge_mentions",
    "knowledge_reviews",
    "knowledge_objects",
)


def _knowledge_tables_stale(conn: sqlite3.Connection) -> bool:
    return any(
        _columns_mismatch(conn, table, expected)
        for table, expected in _EXPECTED_KNOWLEDGE_COLUMNS.items()
    )


# Expected columns for the governance-layer tables (Prompt #10). They hold
# curated state, but on a layout change there is nothing to migrate to, so a
# mismatched table is dropped and recreated. They have no inter-table foreign
# keys, so each can be dropped independently.
_EXPECTED_GOVERNANCE_COLUMNS = {
    "knowledge_owners": {
        "id", "object_id", "owner_type", "owner_id", "assigned_at", "assigned_by",
    },
    "knowledge_lifecycle": {
        "object_id", "name", "object_type", "created_at", "last_seen_at",
        "last_reviewed_at", "last_confirmed_at", "last_confidence",
        "freshness_score", "freshness_state", "review_state", "present",
        "updated_at",
    },
    "knowledge_quality": {
        "object_id", "quality_score", "evidence_score", "review_score",
        "freshness_score", "consistency_score", "owner_score", "confidence_score",
        "evidence_count", "document_count", "computed_at",
    },
    "knowledge_alerts": {
        "id", "alert_type", "severity", "object_id", "message", "status",
        "created_at", "resolved_at",
    },
    "knowledge_change_log": {
        "id", "change_type", "target_kind", "object_id", "field",
        "old_value", "new_value", "detail", "detected_at",
    },
}


def _stale_governance_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        table
        for table, expected in _EXPECTED_GOVERNANCE_COLUMNS.items()
        if _columns_mismatch(conn, table, expected)
    ]


# Expected columns for the compliance-layer tables. Like governance they hold
# curated state with nothing to migrate to, so a mismatched table is dropped and
# recreated. ``compliance_assessment_evidence`` has a FOREIGN KEY to
# ``compliance_assessments``, so the set is dropped children-first.
_EXPECTED_COMPLIANCE_COLUMNS = {
    "compliance_standards": {
        "object_id", "name", "authority", "version", "jurisdiction",
        "effective_from", "source_url", "created_at", "updated_at",
    },
    "compliance_requirements": {
        "object_id", "standard_object_id", "clause_ref", "title",
        "requirement_text", "obligation_level", "assessed_against_version",
        "created_at", "updated_at",
    },
    "compliance_assessments": {
        "id", "requirement_object_id", "control_object_id", "status",
        "assessed_against_version", "rationale", "assessor", "assessed_at",
        "review_status", "created_at", "updated_at",
    },
    "compliance_assessment_evidence": {
        "id", "assessment_id", "artifact_id", "quote", "clause_ref",
        "page_number", "confidence", "created_at",
    },
    "compliance_runs": {
        "id", "started_at", "finished_at", "requirements_assessed",
        "satisfied", "partial", "gaps", "not_applicable", "coverage", "errors",
    },
}

# Drop order: child (evidence) before the assessments it references.
_COMPLIANCE_DROP_ORDER = (
    "compliance_assessment_evidence",
    "compliance_assessments",
    "compliance_requirements",
    "compliance_standards",
    "compliance_runs",
)


def _compliance_tables_stale(conn: sqlite3.Connection) -> bool:
    return any(
        _columns_mismatch(conn, table, expected)
        for table, expected in _EXPECTED_COMPLIANCE_COLUMNS.items()
    )


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Create the schema, rebuilding the local index if it predates this layout.

    The catalog is a regenerable index over source files, so when an older
    schema is detected we drop and recreate rather than attempt an in-place
    migration. Source documents are never touched.
    """

    with connect(db_path) as conn:
        if _needs_rebuild(conn):
            conn.executescript(
                "DROP TABLE IF EXISTS links;"
                "DROP TABLE IF EXISTS link_scan_runs;"
                "DROP TABLE IF EXISTS scan_runs;"
                "DROP TABLE IF EXISTS artifacts;"
            )
        elif _needs_links_rebuild(conn):
            # The links layout changed independently of artifacts; the links
            # table is fully regenerable from the cache via ``discover-links``.
            conn.executescript("DROP TABLE IF EXISTS links;")
        # Semantic tables evolve independently and are regenerable via
        # ``catalog classify``; drop any whose columns no longer match.
        for table in _stale_semantic_tables(conn):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        # Knowledge tables are regenerable via ``catalog consolidate``; if any of
        # them changed shape, drop the whole set (children first) and recreate.
        if _knowledge_tables_stale(conn):
            for table in _KNOWLEDGE_DROP_ORDER:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        # Governance tables (Prompt #10) evolve independently; drop any whose
        # columns no longer match so the recreated schema is authoritative.
        for table in _stale_governance_tables(conn):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        # Compliance tables evolve independently; if any changed shape, drop the
        # whole set (children first) so the recreated schema is authoritative.
        if _compliance_tables_stale(conn):
            for table in _COMPLIANCE_DROP_ORDER:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)


def existing_artifacts(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Return the currently indexed artifacts keyed by source path."""

    return {row["path"]: row for row in conn.execute("SELECT * FROM artifacts")}


def upsert_artifact(conn: sqlite3.Connection, artifact: dict) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(path,id,filename,file_type,size_bytes,created_at,modified_at,sha256,source_system,scan_status,first_seen_at,last_scanned_at)
        VALUES(:path,:id,:filename,:file_type,:size_bytes,:created_at,:modified_at,:sha256,:source_system,:scan_status,:first_seen_at,:last_scanned_at)
        ON CONFLICT(path) DO UPDATE SET
          id=excluded.id, filename=excluded.filename, file_type=excluded.file_type, size_bytes=excluded.size_bytes,
          created_at=excluded.created_at, modified_at=excluded.modified_at, sha256=excluded.sha256,
          source_system=excluded.source_system, scan_status=excluded.scan_status, last_scanned_at=excluded.last_scanned_at
        """,
        artifact,
    )


def mark_deleted(conn: sqlite3.Connection, path: str, scanned_at: str) -> None:
    conn.execute(
        "UPDATE artifacts SET scan_status='DELETED', last_scanned_at=? WHERE path=?",
        (scanned_at, path),
    )


def record_scan_run(conn: sqlite3.Connection, started_at: str, finished_at: str, stats: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO scan_runs(started_at,finished_at,files_scanned,new_files,changed_files,unchanged_files,duplicate_files,deleted_files)
        VALUES(:started_at,:finished_at,:files_scanned,:new_files,:changed_files,:unchanged_files,:duplicate_files,:deleted_files)
        """,
        {"started_at": started_at, "finished_at": finished_at, **stats},
    )
    return int(cur.lastrowid)


def latest_scan_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
