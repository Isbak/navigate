"""Tests for code-aware indexing: detection, structure, chunking, and classify."""

import json
import sqlite3

from catalog.code import (
    chunk_code,
    detect_language,
    extract_structure,
    select_chunks,
    structure_to_result,
)
from catalog.code import chunking as code_chunking
from catalog.code import structure as code_structure
from catalog.scanner import scan
from catalog.semantic.code_prompts import build_code_classification_prompt
from catalog.semantic.parser import parse_classification_response
from catalog.semantic.providers.base import BaseLLMProvider
from catalog.semantic.service import classify_documents

PY_SOURCE = '''"""Example module."""
import os
from sys import path as p

CONST = 1


def top_level(a, b=2):
    return a + b


class Service(Base):
    """A service."""

    def handle(self, req):
        return self._do(req)

    def _do(self, req):
        return req
'''


# --- language detection -----------------------------------------------------


def test_detect_language_maps_known_extensions():
    assert detect_language("a.py") == "python"
    assert detect_language("a.PY") == "python"
    assert detect_language("a.ts") == "typescript"
    assert detect_language("a.tsx") == "tsx"
    assert detect_language("a.go") == "go"
    assert detect_language("a.rs") == "rust"


def test_detect_language_unknown_is_none():
    assert detect_language("a.md") is None
    assert detect_language("a.unknownext") is None
    assert detect_language("README") is None


# --- structure extraction ---------------------------------------------------


def test_extract_structure_python():
    st = extract_structure(PY_SOURCE, "python")
    assert st.parsed is True
    assert st.imports == ("import os", "from sys import path as p")

    assert [c.name for c in st.classes] == ["Service"]
    cls = st.classes[0]
    assert cls.start_line == 12 and cls.end_line == 19  # 1-based span
    assert cls.public is True

    assert [f.name for f in st.functions] == ["top_level"]

    methods = {(m.parent, m.name, m.public) for m in st.methods}
    assert methods == {("Service", "handle", True), ("Service", "_do", False)}


def test_extract_structure_no_language_is_empty():
    st = extract_structure(PY_SOURCE, None)
    assert st.parsed is False
    assert st.is_empty()


def test_extract_structure_unsupported_grammar_is_empty():
    # A language we have no grammar wheel for yields an empty, unparsed outline
    # rather than raising.
    st = extract_structure("IDENTIFICATION DIVISION.", "cobol")
    assert st.parsed is False
    assert st.is_empty()


# --- chunking ---------------------------------------------------------------


def test_chunk_code_keeps_functions_whole_and_preserves_bytes():
    chunks = chunk_code(PY_SOURCE, "python", 80, 0)
    assert len(chunks) > 1  # actually split
    assert max(len(c) for c in chunks) <= 80
    # No function header is sliced across a boundary.
    for marker in ("def top_level", "def handle", "def _do"):
        assert sum(c.count(marker) for c in chunks) == 1
    # Every byte is preserved in order.
    assert "".join(chunks) == PY_SOURCE


def test_chunk_code_oversized_construct_is_split():
    big = "def huge():\n" + "    x = 1\n" * 200
    chunks = chunk_code(big, "python", 60, 0)
    assert len(chunks) > 1
    assert max(len(c) for c in chunks) <= 60
    assert "".join(chunks) == big


def test_chunk_code_unknown_language_falls_back_to_char_chunks():
    text = "x" * 200
    chunks = chunk_code(text, "cobol", 50, 0)
    assert chunks == [text[i : i + 50] for i in range(0, 200, 50)]


def test_select_chunks_dispatches_on_language():
    # Non-code: character chunking regardless of content.
    assert select_chunks("abcdef", None, 3, 0) == ["abc", "def"]
    # Code: boundary-aware, here a single small construct stays whole.
    assert select_chunks("def f():\n    return 1\n", "python", 1000, 0) == [
        "def f():\n    return 1\n"
    ]


# --- structure -> classification result -------------------------------------


def test_structure_to_result_builds_entities_and_relationships():
    st = extract_structure(PY_SOURCE, "python")
    res = structure_to_result(st, {"filename": "example.py"})

    assert res.document_type == "Source Code"
    assert res.type_confidence == 0.0  # lets the LLM win document-level fields

    by_type = {(e.entity_type, e.name) for e in res.entities}
    assert ("Module", "example.py") in by_type
    assert ("Class", "Service") in by_type
    assert ("Function", "top_level") in by_type
    assert ("Function", "Service.handle") in by_type
    assert ("Library", "os") in by_type
    assert ("Library", "sys") in by_type

    rels = {(r.subject, r.predicate, r.object) for r in res.relationships}
    assert ("example.py", "defines", "Service") in rels
    assert ("example.py", "imports", "os") in rels
    assert ("Service", "defines", "Service.handle") in rels


# --- code classification prompt ---------------------------------------------


def test_build_code_prompt_is_deterministic_and_parseable():
    meta = {"filename": "svc.py", "language": "python"}
    system_a, user_a = build_code_classification_prompt(meta, PY_SOURCE)
    system_b, user_b = build_code_classification_prompt(meta, PY_SOURCE)
    assert system_a == system_b  # constant -> cacheable
    assert "svc.py" in user_a and "python" in user_a
    assert "BEGIN SOURCE CODE" in user_a

    # A code-shaped response parses cleanly and keeps the new vocabulary.
    payload = {
        "document_type": "Source Code",
        "type_confidence": 0.9,
        "entities": [{"entity_type": "Module", "name": "svc", "confidence": 0.8}],
        "relationships": [
            {
                "subject": "svc",
                "predicate": "imports",
                "object": "os",
                "confidence": 0.8,
            }
        ],
    }
    result = parse_classification_response(json.dumps(payload))
    assert result.document_type == "Source Code"
    assert result.entities[0].entity_type == "Module"
    assert result.relationships[0].predicate == "imports"


# --- scanner ingestion ------------------------------------------------------


def _write_config(tmp_path, root, index_code=True):
    config = tmp_path / "sources.yml"
    config.write_text(
        f"sources:\n  - path: '{root}'\n    source_system: 'test'\n"
        f"index_code: {str(index_code).lower()}\nexclude: []\n",
        encoding="utf-8",
    )
    return config


def test_scan_ingests_code_with_structure_and_language(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "service.py").write_text(PY_SOURCE, encoding="utf-8")
    (repo / "notes.md").write_text("# notes", encoding="utf-8")
    # A vendored file under a default code exclude must be skipped.
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.js").write_text("export const x = 1;\n", encoding="utf-8")

    config = _write_config(tmp_path, repo)
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    scan(config, db, cache)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    names = {r["filename"] for r in conn.execute("SELECT filename FROM artifacts")}
    assert "service.py" in names  # code ingested
    assert "notes.md" in names  # documents still ingested
    assert "dep.js" not in names  # vendored code excluded

    row = conn.execute(
        "SELECT id FROM artifacts WHERE filename = 'service.py'"
    ).fetchone()
    artifact_cache = cache / row["id"]
    meta = json.loads((artifact_cache / "metadata.json").read_text(encoding="utf-8"))
    assert meta["language"] == "python"
    structure = json.loads(
        (artifact_cache / "code_structure.json").read_text(encoding="utf-8")
    )
    assert structure["parsed"] is True
    assert any(c["name"] == "Service" for c in structure["classes"])


def test_scan_can_disable_code_indexing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text(PY_SOURCE, encoding="utf-8")
    (repo / "notes.md").write_text("# notes", encoding="utf-8")

    config = _write_config(tmp_path, repo, index_code=False)
    db = tmp_path / "catalog.sqlite"
    scan(config, db, tmp_path / "cache")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    names = {r["filename"] for r in conn.execute("SELECT filename FROM artifacts")}
    assert names == {"notes.md"}  # code skipped when disabled


# --- end-to-end classification ----------------------------------------------


class _CodeStubProvider(BaseLLMProvider):
    """Returns a fixed code classification for any prompt."""

    def __init__(self):
        super().__init__("code-stub")
        self.systems: list[str] = []

    def generate(self, prompt, *, system=None):
        self.systems.append(system or "")
        return json.dumps(
            {
                "document_type": "Source Code",
                "type_confidence": 0.95,
                "short_summary": "A service module.",
                "entities": [
                    {"entity_type": "Service", "name": "Service", "confidence": 0.9}
                ],
                "relationships": [
                    {
                        "subject": "Service",
                        "predicate": "calls",
                        "object": "Base",
                        "confidence": 0.7,
                    }
                ],
            }
        )


def _write_code_cache(cache_dir, artifact_id, text, language="python"):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps(
            {"artifact_id": artifact_id, "filename": "service.py", "language": language}
        ),
        encoding="utf-8",
    )


def test_classify_code_merges_llm_and_structure(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_code_cache(cache, "doc_code", PY_SOURCE)

    provider = _CodeStubProvider()
    stats = classify_documents(db, cache, provider, max_input_chars=1000)

    assert stats.documents_processed == 1
    # The code system prompt (not the document one) was used.
    assert any("staff software engineer" in s for s in provider.systems)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    entities = {
        (r["entity_type"], r["name"])
        for r in conn.execute("SELECT entity_type, name FROM candidate_entities")
    }
    # Deterministic structure entities are persisted...
    assert ("Module", "service.py") in entities
    assert ("Class", "Service") in entities
    assert ("Function", "top_level") in entities
    # ...alongside the LLM's semantic proposal.
    assert ("Service", "Service") in entities

    doctype = conn.execute(
        "SELECT document_type FROM document_classifications"
    ).fetchone()[0]
    assert doctype == "Source Code"


def test_classify_code_degrades_without_grammar(tmp_path, monkeypatch):
    # Force tree-sitter to be unavailable: classification must still succeed via
    # the char-chunk fallback and an empty structure (just the Module entity).
    # Patch where the symbol is used, not just where it is defined.
    monkeypatch.setattr(code_structure, "get_parser", lambda language: None)
    monkeypatch.setattr(code_chunking, "get_parser", lambda language: None)

    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_code_cache(cache, "doc_code", PY_SOURCE)

    provider = _CodeStubProvider()
    stats = classify_documents(db, cache, provider, max_input_chars=1000)
    assert stats.documents_processed == 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    entities = {
        (r["entity_type"], r["name"])
        for r in conn.execute("SELECT entity_type, name FROM candidate_entities")
    }
    # No parsed classes/functions, but the module and the LLM proposal remain.
    assert ("Module", "service.py") in entities
    assert ("Service", "Service") in entities
    assert ("Class", "Service") not in entities
