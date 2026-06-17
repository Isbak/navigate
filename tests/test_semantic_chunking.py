import json

from catalog.semantic.models import (
    CandidateEntity,
    CandidateEquation,
    ClassificationResult,
)
from catalog.semantic.parser import merge_classification_results
from catalog.semantic.prompts import chunk_text
from catalog.semantic.providers.base import BaseLLMProvider
from catalog.semantic.service import classify_documents


class ChunkProvider(BaseLLMProvider):
    """Returns a different payload depending on which chunk marker it sees."""

    def __init__(self, by_marker, default=None):
        super().__init__("chunk-model")
        self.by_marker = by_marker
        self.default = default or {"document_type": "Other", "type_confidence": 0.1}
        self.calls = 0

    def generate(self, prompt, *, system=None):
        self.calls += 1
        for marker, payload in self.by_marker.items():
            if marker in prompt:
                return json.dumps(payload)
        return json.dumps(self.default)


def _write_cache(cache_dir, artifact_id, text):
    d = cache_dir / artifact_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "extracted.txt").write_text(text, encoding="utf-8")
    (d / "metadata.json").write_text(
        json.dumps({"artifact_id": artifact_id, "filename": artifact_id}),
        encoding="utf-8",
    )


def test_chunk_text_splits_with_overlap():
    assert chunk_text("abcdefghij", 4, 0) == ["abcd", "efgh", "ij"]
    # Short text stays a single chunk (unchanged behavior).
    assert chunk_text("short", 100, 10) == ["short"]
    # Overlap keeps boundary content in two chunks.
    chunks = chunk_text("abcdefghij", 4, 2)
    assert chunks[0] == "abcd" and chunks[1][0] == "c"


ALPHA = {
    "document_type": "Report",
    "type_confidence": 0.4,
    "entities": [{"entity_type": "Technology", "name": "SAP", "confidence": 0.9}],
}
BETA = {
    "document_type": "Strategy",
    "type_confidence": 0.8,
    "entities": [{"entity_type": "Technology", "name": "AWS", "confidence": 0.8}],
}


def test_classify_merges_entities_from_every_chunk(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    text = "ALPHA " + ("x" * 40) + " BETA"  # ALPHA in chunk 0, BETA in chunk 1
    _write_cache(cache, "doc_long", text)

    provider = ChunkProvider({"ALPHA": ALPHA, "BETA": BETA})
    stats = classify_documents(db, cache, provider, max_input_chars=30, chunk_overlap=0)

    assert provider.calls == 2  # processed both chunks
    assert stats.entities == 2  # SAP (chunk 0) + AWS (chunk 1) both survive


def test_classify_dedupes_repeated_entities(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    text = "ALPHA " + ("x" * 40) + " ALPHA"  # same marker in both chunks
    _write_cache(cache, "doc_dup", text)

    provider = ChunkProvider({"ALPHA": ALPHA})
    stats = classify_documents(db, cache, provider, max_input_chars=30, chunk_overlap=0)

    assert provider.calls == 2
    assert stats.entities == 1  # the duplicate SAP entity is collapsed


def test_short_document_is_single_chunk(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_small", "ALPHA only")

    provider = ChunkProvider({"ALPHA": ALPHA})
    stats = classify_documents(db, cache, provider, max_input_chars=1000)

    assert provider.calls == 1
    assert stats.entities == 1


def test_max_chunks_caps_processing(tmp_path):
    db = tmp_path / "catalog.sqlite"
    cache = tmp_path / "cache"
    _write_cache(cache, "doc_capped", "y" * 1000)

    provider = ChunkProvider({})  # default payload
    classify_documents(
        db, cache, provider, max_input_chars=10, chunk_overlap=0, max_chunks=3
    )
    assert provider.calls == 3  # would be 100 chunks without the cap


def test_merge_picks_confident_type_and_dedupes_equations():
    eq = CandidateEquation(clause_ref="6.1", symbol="V", standard_name="EC2")
    eq_dup = CandidateEquation(
        clause_ref="6.1", symbol="V", standard_name="EC2", confidence=0.9
    )
    eq_other = CandidateEquation(clause_ref="6.2", symbol="M", standard_name="EC2")
    a = ClassificationResult(
        document_type="Report",
        type_confidence=0.3,
        entities=[CandidateEntity("Technology", "SAP", 0.5)],
        equations=[eq],
    )
    b = ClassificationResult(
        document_type="Standard",
        type_confidence=0.95,
        equations=[eq_dup, eq_other],
    )

    merged = merge_classification_results([a, b])

    assert merged.document_type == "Standard"  # highest type_confidence wins
    assert len(merged.equations) == 2  # (6.1,V) deduped, (6.2,M) kept
    assert any(e.confidence == 0.9 for e in merged.equations)  # higher-conf kept
    assert len(merged.entities) == 1
