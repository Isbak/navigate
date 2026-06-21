from catalog.semantic.models import DOCUMENT_TYPES, RELATIONSHIP_PREDICATES
from catalog.semantic.prompts import (
    CLASSIFICATION_SYSTEM,
    SYSTEM_PROMPT,
    build_classification_prompt,
)


def test_prompt_includes_metadata_and_text():
    system, user = build_classification_prompt(
        {"filename": "Release Governance v7.pptx", "file_type": "pptx"},
        "The release governance model defines ownership.",
    )
    # The static schema/persona lives in the (cacheable) system prompt; only the
    # per-document metadata and body go in the user message.
    assert system == CLASSIFICATION_SYSTEM
    assert SYSTEM_PROMPT in system
    assert "Release Governance v7.pptx" in user
    assert "pptx" in user
    assert "release governance model" in user


def test_system_prompt_is_constant_across_documents():
    system_a, user_a = build_classification_prompt({"filename": "a.txt"}, "alpha")
    system_b, user_b = build_classification_prompt({"filename": "b.txt"}, "beta")
    # Caching requires a byte-identical prefix, so the system prompt must not vary
    # with the document.
    assert system_a == system_b
    assert user_a != user_b


def test_prompt_lists_controlled_vocabularies():
    system, _ = build_classification_prompt({}, "x")
    for dtype in DOCUMENT_TYPES:
        assert dtype in system
    for predicate in RELATIONSHIP_PREDICATES:
        assert predicate in system


def test_prompt_truncates_long_text():
    long_text = "A" * 5000
    _, user = build_classification_prompt({}, long_text, max_input_chars=100)
    assert "A" * 100 in user
    assert "A" * 101 not in user
    assert "truncated" in user.lower()


def test_prompt_handles_missing_metadata():
    _, user = build_classification_prompt({}, "body")
    assert "unknown" in user  # filename/file_type fallbacks
    assert "body" in user
