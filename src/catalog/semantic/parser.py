"""Parse and validate raw LLM output into a :class:`ClassificationResult`.

LLM output is untrusted text. This module is deliberately defensive:

* it strips ``<think>`` reasoning blocks (qwen and similar) and markdown code
  fences, then extracts the first balanced JSON object;
* it clamps every confidence into ``[0.0, 1.0]`` and drops items whose
  confidence is missing or non-numeric;
* it normalizes ``document_type`` to the controlled vocabulary (falling back to
  "Other") and keeps relationship predicates / entity types only when valid;
* missing arrays become empty lists rather than errors.

A document that the model returns nothing usable for yields a low-confidence
"Other" classification rather than raising, so one bad response never aborts a
batch.
"""

from __future__ import annotations

import json
import logging
import re

from .models import (
    DOCUMENT_TYPES,
    ENTITY_TYPES,
    RELATIONSHIP_PREDICATES,
    CandidateCapability,
    CandidateDecision,
    CandidateEntity,
    CandidateRelationship,
    CandidateRisk,
    ClassificationResult,
    DomainScore,
)

LOGGER = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_DOC_TYPE_LOOKUP = {t.lower(): t for t in DOCUMENT_TYPES}
_ENTITY_TYPE_LOOKUP = {t.lower(): t for t in ENTITY_TYPES}
_PREDICATE_LOOKUP = {p.lower(): p for p in RELATIONSHIP_PREDICATES}


class ParseError(ValueError):
    """Raised when no JSON object can be recovered from a response."""


def validate_confidence(value: object, default: float = 0.0) -> float:
    """Coerce ``value`` to a float clamped into ``[0.0, 1.0]``.

    Non-numeric or missing values return ``default`` (also clamped). This is the
    single choke point for the rule that every confidence is a probability.
    """

    try:
        conf = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        conf = default
    if conf != conf:  # NaN
        conf = default
    return max(0.0, min(1.0, conf))


def _strip_to_json(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "").strip()
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    return cleaned


def _extract_json_object(text: str) -> dict:
    """Return the first balanced ``{...}`` object parsed from ``text``."""

    cleaned = _strip_to_json(text)
    if not cleaned:
        raise ParseError("Empty response")

    # Fast path: the whole thing is JSON.
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Fallback: scan for the first balanced brace span and parse that.
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)

    raise ParseError("No JSON object found in response")


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_document_type(value: object) -> str:
    return _DOC_TYPE_LOOKUP.get(_text(value).lower(), "Other")


def _parse_domains(value: object) -> list[DomainScore]:
    out: list[DomainScore] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            name = _text(item.get("domain") or item.get("name"))
            conf = validate_confidence(item.get("confidence"))
        elif isinstance(item, str):
            name, conf = item.strip(), 0.5
        else:
            continue
        if name:
            out.append(DomainScore(domain=name, confidence=conf))
    return out


def _parse_capabilities(value: object) -> list[CandidateCapability]:
    out: list[CandidateCapability] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            name = _text(item.get("name") or item.get("capability"))
            conf = validate_confidence(item.get("confidence"))
            quote = _text(item.get("supporting_text"))
        elif isinstance(item, str):
            name, conf, quote = item.strip(), 0.5, ""
        else:
            continue
        if name:
            out.append(
                CandidateCapability(name=name, confidence=conf, supporting_text=quote)
            )
    return out


def _parse_entities(value: object) -> list[CandidateEntity]:
    out: list[CandidateEntity] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        etype = _ENTITY_TYPE_LOOKUP.get(_text(item.get("entity_type")).lower(), "Concept")
        out.append(
            CandidateEntity(
                entity_type=etype,
                name=name,
                confidence=validate_confidence(item.get("confidence")),
                supporting_text=_text(item.get("supporting_text")),
            )
        )
    return out


def _parse_decisions(value: object) -> list[CandidateDecision]:
    out: list[CandidateDecision] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            text = _text(item.get("decision_text") or item.get("decision") or item.get("text"))
            conf = validate_confidence(item.get("confidence"))
            quote = _text(item.get("supporting_text"))
        elif isinstance(item, str):
            text, conf, quote = item.strip(), 0.5, ""
        else:
            continue
        if text:
            out.append(
                CandidateDecision(decision_text=text, confidence=conf, supporting_text=quote)
            )
    return out


def _parse_risks(value: object) -> list[CandidateRisk]:
    out: list[CandidateRisk] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            text = _text(item.get("risk_description") or item.get("risk") or item.get("text"))
            conf = validate_confidence(item.get("confidence"))
            quote = _text(item.get("supporting_text"))
        elif isinstance(item, str):
            text, conf, quote = item.strip(), 0.5, ""
        else:
            continue
        if text:
            out.append(
                CandidateRisk(risk_description=text, confidence=conf, supporting_text=quote)
            )
    return out


def _parse_relationships(value: object) -> list[CandidateRelationship]:
    out: list[CandidateRelationship] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        subject = _text(item.get("subject") or item.get("source"))
        obj = _text(item.get("object") or item.get("target"))
        predicate = _PREDICATE_LOOKUP.get(_text(item.get("predicate")).lower())
        if not (subject and obj and predicate):
            continue
        out.append(
            CandidateRelationship(
                subject=subject,
                predicate=predicate,
                object=obj,
                confidence=validate_confidence(item.get("confidence")),
                supporting_text=_text(item.get("supporting_text")),
            )
        )
    return out


def parse_classification_response(text: str) -> ClassificationResult:
    """Parse raw model output into a validated :class:`ClassificationResult`."""

    data = _extract_json_object(text)
    return ClassificationResult(
        document_type=_normalize_document_type(data.get("document_type")),
        type_confidence=validate_confidence(data.get("type_confidence")),
        short_summary=_text(data.get("short_summary"))[:2000],
        long_summary=_text(data.get("long_summary"))[:8000],
        domains=_parse_domains(data.get("domains")),
        entities=_parse_entities(data.get("entities")),
        capabilities=_parse_capabilities(data.get("capabilities")),
        decisions=_parse_decisions(data.get("decisions")),
        risks=_parse_risks(data.get("risks")),
        relationships=_parse_relationships(data.get("relationships")),
    )


__all__ = [
    "ParseError",
    "validate_confidence",
    "parse_classification_response",
]
