import json

import pytest

from catalog.semantic.parser import (
    ParseError,
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


def test_empty_response_raises():
    with pytest.raises(ParseError):
        parse_classification_response("   ")


def test_no_json_raises():
    with pytest.raises(ParseError):
        parse_classification_response("no json at all here")
