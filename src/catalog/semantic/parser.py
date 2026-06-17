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

from .equation_ast import analyze_equation
from .models import (
    DOCUMENT_TYPES,
    ENTITY_TYPES,
    OBLIGATION_LEVELS,
    RELATIONSHIP_PREDICATES,
    CandidateCapability,
    CandidateDecision,
    CandidateEntity,
    CandidateEquation,
    CandidateRelationship,
    CandidateRequirement,
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
_OBLIGATION_LOOKUP = {o.lower(): o for o in OBLIGATION_LEVELS}


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


def _normalize_obligation(value: object) -> str:
    return _OBLIGATION_LOOKUP.get(_text(value).lower(), "MANDATORY")


def _parse_requirements(value: object) -> list[CandidateRequirement]:
    out: list[CandidateRequirement] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text") or item.get("requirement_text"))
        clause = _text(item.get("clause_ref") or item.get("clause") or item.get("ref"))
        title = _text(item.get("title") or item.get("name"))
        # A requirement needs at least an obligation text or a clause locator to
        # be useful; drop empty rows the way the other candidate parsers do.
        if not (text or clause or title):
            continue
        out.append(
            CandidateRequirement(
                clause_ref=clause,
                title=title,
                text=text,
                standard_name=_text(item.get("standard_name") or item.get("standard")),
                standard_version=_text(item.get("standard_version") or item.get("version")),
                obligation_level=_normalize_obligation(item.get("obligation_level")),
                confidence=validate_confidence(item.get("confidence")),
                supporting_text=_text(item.get("supporting_text")),
            )
        )
    return out


def _parse_variables(value: object) -> list[dict]:
    """Normalize an equation's variable list into ``{symbol, description, unit}``."""

    out: list[dict] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            symbol = _text(item.get("symbol") or item.get("name"))
            if not symbol:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "description": _text(item.get("description") or item.get("meaning")),
                    "unit": _text(item.get("unit") or item.get("units")),
                }
            )
        elif isinstance(item, str) and item.strip():
            out.append({"symbol": item.strip(), "description": "", "unit": ""})
    return out


def _parse_equations(value: object) -> list[CandidateEquation]:
    """Parse equation proposals and validate each formula without executing it.

    Every formula is run through :func:`analyze_equation`, which parses it with
    ``ast`` and checks it against a strict allowlist. An invalid formula is kept
    (``valid=False`` with a note) so a human still reviews it, mirroring how the
    other parsers keep low-confidence rows rather than dropping them.
    """

    out: list[CandidateEquation] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        expression = _text(item.get("expression") or item.get("formula"))
        python_code = _text(item.get("python_code") or item.get("python"))
        symbol = _text(item.get("symbol") or item.get("result") or item.get("name"))
        clause = _text(item.get("clause_ref") or item.get("clause") or item.get("ref"))
        title = _text(item.get("title"))
        # An equation needs a formula or at least a symbol/clause to be useful.
        if not (expression or python_code or symbol or clause):
            continue
        analysis = analyze_equation(
            expression=expression, symbol=symbol, python_code=python_code
        )
        out.append(
            CandidateEquation(
                clause_ref=clause,
                symbol=symbol,
                title=title,
                expression=analysis.expression or expression,
                python_code=analysis.function_code,
                ast_json=analysis.ast_json,
                variables=_parse_variables(item.get("variables")),
                latex=_text(item.get("latex") or item.get("notation")),
                standard_name=_text(item.get("standard_name") or item.get("standard")),
                standard_version=_text(item.get("standard_version") or item.get("version")),
                valid=analysis.valid,
                validation_note=analysis.note,
                confidence=validate_confidence(item.get("confidence")),
                supporting_text=_text(item.get("supporting_text")),
            )
        )
    return out


def _dedupe(items: list, key) -> list:
    """Keep the highest-confidence item per natural key, preserving order."""

    best: dict = {}
    for item in items:
        k = key(item)
        current = best.get(k)
        if current is None or item.confidence > current.confidence:
            best[k] = item
    return list(best.values())


def merge_classification_results(
    results: list[ClassificationResult],
) -> ClassificationResult:
    """Merge per-chunk results for one document into a single result.

    The document-level fields (type and summaries) are taken from the chunk the
    model was most confident about; the list fields are concatenated and then
    deduped by a natural key, keeping the highest-confidence instance. This lets
    equations/entities discovered deep in a long document survive alongside
    those found near the top.
    """

    if not results:
        return ClassificationResult(document_type="Other", type_confidence=0.0)
    if len(results) == 1:
        return results[0]

    primary = max(results, key=lambda r: r.type_confidence)

    domains: list[DomainScore] = []
    entities: list[CandidateEntity] = []
    capabilities: list[CandidateCapability] = []
    decisions: list[CandidateDecision] = []
    risks: list[CandidateRisk] = []
    relationships: list[CandidateRelationship] = []
    requirements: list[CandidateRequirement] = []
    equations: list[CandidateEquation] = []
    for r in results:
        domains += r.domains
        entities += r.entities
        capabilities += r.capabilities
        decisions += r.decisions
        risks += r.risks
        relationships += r.relationships
        requirements += r.requirements
        equations += r.equations

    return ClassificationResult(
        document_type=primary.document_type,
        type_confidence=primary.type_confidence,
        short_summary=primary.short_summary,
        long_summary=primary.long_summary,
        domains=_dedupe(domains, lambda d: d.domain.lower()),
        entities=_dedupe(entities, lambda e: (e.entity_type, e.name.lower())),
        capabilities=_dedupe(capabilities, lambda c: c.name.lower()),
        decisions=_dedupe(decisions, lambda d: d.decision_text.lower()),
        risks=_dedupe(risks, lambda r: r.risk_description.lower()),
        relationships=_dedupe(
            relationships, lambda x: (x.subject.lower(), x.predicate, x.object.lower())
        ),
        requirements=_dedupe(
            requirements, lambda q: (q.standard_name.lower(), q.clause_ref.lower())
        ),
        equations=_dedupe(
            equations,
            lambda e: (e.standard_name.lower(), e.clause_ref.lower(), e.symbol.lower()),
        ),
    )


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
        requirements=_parse_requirements(data.get("requirements")),
        equations=_parse_equations(data.get("equations")),
    )


__all__ = [
    "ParseError",
    "validate_confidence",
    "parse_classification_response",
    "merge_classification_results",
]
