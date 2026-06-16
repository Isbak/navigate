"""Tests for requirement extraction in the semantic parser."""

from __future__ import annotations

from catalog.semantic.parser import parse_classification_response


def test_parses_requirements_array():
    raw = """
    {
      "document_type": "Standard",
      "type_confidence": 0.95,
      "requirements": [
        {"standard_name": "ISO 27001", "standard_version": "2022",
         "clause_ref": "A.8.24", "title": "Use of cryptography",
         "text": "Rules for the use of cryptography shall be defined.",
         "obligation_level": "MANDATORY", "confidence": 0.9,
         "supporting_text": "shall be defined"}
      ]
    }
    """
    result = parse_classification_response(raw)
    assert result.document_type == "Standard"
    assert len(result.requirements) == 1
    req = result.requirements[0]
    assert req.standard_name == "ISO 27001"
    assert req.clause_ref == "A.8.24"
    assert req.obligation_level == "MANDATORY"
    assert 0.0 <= req.confidence <= 1.0


def test_unknown_obligation_normalizes_to_mandatory():
    raw = """
    {"document_type": "Regulation", "type_confidence": 0.8,
     "requirements": [{"clause_ref": "5.1", "title": "t", "text": "must do x",
                       "obligation_level": "weird", "confidence": 0.7}]}
    """
    result = parse_classification_response(raw)
    assert result.requirements[0].obligation_level == "MANDATORY"


def test_empty_or_malformed_requirements_are_dropped():
    raw = """
    {"document_type": "Other", "type_confidence": 0.5,
     "requirements": [{"confidence": 0.9}, "not-an-object",
                      {"clause_ref": "A.1", "title": "", "text": ""}]}
    """
    result = parse_classification_response(raw)
    # First has no clause/title/text -> dropped; string -> dropped; third kept.
    assert len(result.requirements) == 1
    assert result.requirements[0].clause_ref == "A.1"


def test_missing_requirements_key_yields_empty_list():
    raw = '{"document_type": "Strategy", "type_confidence": 0.6}'
    result = parse_classification_response(raw)
    assert result.requirements == []
