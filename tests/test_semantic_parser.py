import json

import pytest

from catalog.semantic.parser import (
    ParseError,
    merge_classification_results,
    parse_classification_response,
    validate_confidence,
)

FULL = {
    "document_type": "Governance",
    "type_confidence": 0.93,
    "short_summary": "short",
    "long_summary": "long",
    "domains": [
        {"domain": "Test & Release", "confidence": 0.9},
        {"domain": "Digital Transformation", "confidence": 0.7},
    ],
    "capabilities": [
        {"name": "Release Management", "confidence": 0.92, "supporting_text": "q1"},
    ],
    "entities": [
        {"entity_type": "Technology", "name": "SAP", "confidence": 0.8},
        {"entity_type": "Concept", "name": "Launchpad Model", "confidence": 0.85},
    ],
    "decisions": [
        {"decision_text": "Use Launchpad model", "confidence": 0.84, "supporting_text": "q2"},
    ],
    "risks": [
        {"risk_description": "Unclear ownership", "confidence": 0.7},
    ],
    "relationships": [
        {"subject": "Release Governance", "predicate": "supports",
         "object": "Launchpad Model", "confidence": 0.87},
    ],
}


def test_validate_confidence_clamps_and_defaults():
    assert validate_confidence(0.5) == 0.5
    assert validate_confidence(1.5) == 1.0
    assert validate_confidence(-3) == 0.0
    assert validate_confidence("not a number") == 0.0
    assert validate_confidence(None, default=0.2) == 0.2
    assert validate_confidence(float("nan")) == 0.0


def test_parse_full_response():
    result = parse_classification_response(json.dumps(FULL))
    assert result.document_type == "Governance"
    assert result.type_confidence == 0.93
    assert [d.domain for d in result.domains] == ["Test & Release", "Digital Transformation"]
    assert result.capabilities[0].name == "Release Management"
    assert result.capabilities[0].supporting_text == "q1"
    assert {e.entity_type for e in result.entities} == {"Technology", "Concept"}
    assert result.decisions[0].decision_text == "Use Launchpad model"
    assert result.risks[0].risk_description == "Unclear ownership"
    rel = result.relationships[0]
    assert (rel.subject, rel.predicate, rel.object) == (
        "Release Governance", "supports", "Launchpad Model"
    )


def test_parse_strips_think_blocks_and_fences():
    raw = "<think>reasoning here</think>\n```json\n" + json.dumps(FULL) + "\n```"
    result = parse_classification_response(raw)
    assert result.document_type == "Governance"


def test_parse_extracts_embedded_object():
    raw = "Sure! Here is the result:\n" + json.dumps(FULL) + "\nHope that helps."
    result = parse_classification_response(raw)
    assert result.document_type == "Governance"


def test_unknown_document_type_falls_back_to_other():
    result = parse_classification_response(json.dumps({"document_type": "Memo"}))
    assert result.document_type == "Other"


def test_invalid_predicate_dropped():
    raw = json.dumps(
        {
            "document_type": "Report",
            "relationships": [
                {"subject": "A", "predicate": "frobnicates", "object": "B", "confidence": 0.9},
                {"subject": "A", "predicate": "supports", "object": "B", "confidence": 0.9},
            ],
        }
    )
    result = parse_classification_response(raw)
    assert len(result.relationships) == 1
    assert result.relationships[0].predicate == "supports"


def test_confidence_clamped_in_parsed_items():
    raw = json.dumps(
        {
            "document_type": "Report",
            "type_confidence": 5,
            "capabilities": [{"name": "X", "confidence": 2.0}],
        }
    )
    result = parse_classification_response(raw)
    assert result.type_confidence == 1.0
    assert result.capabilities[0].confidence == 1.0


def test_missing_arrays_become_empty():
    result = parse_classification_response(json.dumps({"document_type": "Report"}))
    assert result.domains == []
    assert result.decisions == []
    assert result.relationships == []


def test_decision_and_risk_titles_parsed():
    raw = json.dumps(
        {
            "document_type": "Governance",
            "decisions": [
                {"title": "Adopt Launchpad model",
                 "decision_text": "We will adopt the Launchpad operating model",
                 "confidence": 0.8},
            ],
            "risks": [
                {"title": "Unclear ownership",
                 "risk_description": "Ownership between teams is undefined",
                 "confidence": 0.6},
            ],
        }
    )
    result = parse_classification_response(raw)
    assert result.decisions[0].title == "Adopt Launchpad model"
    assert result.decisions[0].decision_text.startswith("We will adopt")
    assert result.risks[0].title == "Unclear ownership"


def test_missing_decision_title_is_derived():
    # When the model omits a title, a short label is derived from the full text
    # so the decision still has a stable, mergeable key (not a unique sentence).
    raw = json.dumps(
        {
            "document_type": "Report",
            "decisions": [
                {"decision_text": "Adopt the Launchpad model; migrate by Q3 2026",
                 "confidence": 0.7},
            ],
        }
    )
    result = parse_classification_response(raw)
    title = result.decisions[0].title
    assert title == "Adopt the Launchpad model"  # leading clause, capped
    assert len(title.split()) <= 8


def test_merge_dedupes_decisions_by_title():
    # Two chunks propose the same decision with differently-worded full text but
    # the same title; the merge keeps a single decision keyed on the title.
    a = parse_classification_response(json.dumps(
        {"document_type": "Report", "decisions": [
            {"title": "Adopt Launchpad", "decision_text": "Adopt Launchpad now",
             "confidence": 0.6}]}
    ))
    b = parse_classification_response(json.dumps(
        {"document_type": "Report", "decisions": [
            {"title": "Adopt Launchpad", "decision_text": "We adopt Launchpad",
             "confidence": 0.9}]}
    ))
    merged = merge_classification_results([a, b])
    assert len(merged.decisions) == 1
    assert merged.decisions[0].confidence == 0.9  # highest-confidence kept


def test_empty_response_raises():
    with pytest.raises(ParseError):
        parse_classification_response("   ")


def test_no_json_raises():
    with pytest.raises(ParseError):
        parse_classification_response("no json at all here")
