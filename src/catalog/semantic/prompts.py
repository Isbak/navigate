"""Prompt construction for document classification.

The prompt asks the model to act as an analyst that *proposes* structured
knowledge from a single document and to return a single JSON object. It never
asks the model to assert facts: the instructions stress confidence scores and
supporting quotes, mirroring the storage model where everything starts as an
unreviewed observation or hypothesis.

``build_classification_prompt`` is pure and deterministic given its inputs, so
it is straightforward to unit test without a live model.
"""

from __future__ import annotations

import json

from .equation_ast import ALLOWED_FUNCTIONS
from .models import (
    DOCUMENT_TYPES,
    ENTITY_TYPES,
    OBLIGATION_LEVELS,
    RELATIONSHIP_PREDICATES,
)

SYSTEM_PROMPT = (
    "You are a meticulous knowledge analyst. You read a single business or "
    "technical document and propose structured knowledge about it. You never "
    "invent facts: every item you return includes a confidence score between "
    "0.0 and 1.0 and, where possible, a short supporting quote copied verbatim "
    "from the document. When unsure, lower the confidence rather than omitting "
    "the item. Respond with a single JSON object and nothing else."
)

# The example output is a teaching aid for the model; it is not parsed.
_EXAMPLE = {
    "document_type": "Governance",
    "type_confidence": 0.93,
    "short_summary": "Defines the release governance model and its operating cadence.",
    "long_summary": "A longer multi-sentence summary of up to 500 words ...",
    "domains": [
        {"domain": "Test & Release", "confidence": 0.9},
        {"domain": "Digital Transformation", "confidence": 0.7},
    ],
    "capabilities": [
        {"name": "Release Management", "confidence": 0.92, "supporting_text": "..."},
    ],
    "entities": [
        {"entity_type": "Concept", "name": "Launchpad Model", "confidence": 0.85,
         "supporting_text": "..."},
        {"entity_type": "Technology", "name": "SAP", "confidence": 0.8,
         "supporting_text": "..."},
    ],
    "decisions": [
        {"decision_text": "Adopt the Launchpad/Mission operating model",
         "confidence": 0.84, "supporting_text": "We will move to a Launchpad model"},
    ],
    "risks": [
        {"risk_description": "Unclear ownership between teams", "confidence": 0.7,
         "supporting_text": "ownership of the release process is undefined"},
    ],
    "relationships": [
        {"subject": "Release Governance", "predicate": "supports",
         "object": "Launchpad Model", "confidence": 0.87, "supporting_text": "..."},
    ],
    "requirements": [
        {"standard_name": "ISO 27001", "standard_version": "2022",
         "clause_ref": "A.8.24", "title": "Use of cryptography",
         "text": "Rules for the effective use of cryptography shall be defined "
                 "and implemented.", "obligation_level": "MANDATORY",
         "confidence": 0.9, "supporting_text": "..."},
    ],
    "equations": [
        {"standard_name": "EN 1992-1-1", "standard_version": "2004",
         "clause_ref": "6.2.2(1)", "symbol": "V_Rd_c",
         "title": "Design shear resistance",
         "expression": "C_Rd_c * k * (100 * rho_l * f_ck) ** (1/3) * b_w * d",
         "variables": [
             {"symbol": "C_Rd_c", "description": "empirical coefficient", "unit": "-"},
             {"symbol": "k", "description": "size effect factor", "unit": "-"},
             {"symbol": "rho_l", "description": "longitudinal reinforcement ratio", "unit": "-"},
             {"symbol": "f_ck", "description": "characteristic concrete strength", "unit": "MPa"},
             {"symbol": "b_w", "description": "web width", "unit": "mm"},
             {"symbol": "d", "description": "effective depth", "unit": "mm"},
         ],
         "latex": "V_{Rd,c} = C_{Rd,c} k (100 \\rho_l f_{ck})^{1/3} b_w d",
         "confidence": 0.88, "supporting_text": "..."},
    ],
}


def _bullet(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def build_classification_prompt(
    metadata: dict,
    text: str,
    *,
    max_input_chars: int = 12000,
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for one document.

    ``metadata`` is the artifact's ``metadata.json`` (filename, file_type, ...);
    ``text`` is the extracted document text, truncated to ``max_input_chars``.
    """

    body = text or ""
    truncated = len(body) > max_input_chars
    if truncated:
        body = body[:max_input_chars]

    filename = metadata.get("filename", metadata.get("artifact_id", "unknown"))
    file_type = metadata.get("file_type", "unknown")

    instructions = f"""\
Analyze the document below and return a single JSON object with these keys:

- "document_type": one of [{_bullet(DOCUMENT_TYPES)}].
- "type_confidence": number 0.0-1.0 for the document_type.
- "short_summary": <= 100 words.
- "long_summary": <= 500 words.
- "domains": array of {{"domain": str, "confidence": 0.0-1.0}}. A domain is a
  business or technology area such as "Test & Release", "Architecture", "SAP",
  "Data", "Finance", "HR". Include every domain the document touches.
- "capabilities": array of {{"name": str, "confidence": 0.0-1.0,
  "supporting_text": str}}. Business capabilities discussed, e.g. "Release
  Management", "Change Management", "Incident Management".
- "entities": array of {{"entity_type": str, "name": str, "confidence":
  0.0-1.0, "supporting_text": str}}. entity_type is one of
  [{_bullet(ENTITY_TYPES)}].
- "decisions": array of {{"decision_text": str, "confidence": 0.0-1.0,
  "supporting_text": str}}. Decisions the document appears to make. Do NOT mark
  anything as approved; these are candidates only.
- "risks": array of {{"risk_description": str, "confidence": 0.0-1.0,
  "supporting_text": str}}.
- "relationships": array of {{"subject": str, "predicate": str, "object": str,
  "confidence": 0.0-1.0, "supporting_text": str}}. predicate is one of
  [{_bullet(RELATIONSHIP_PREDICATES)}].
- "requirements": array of {{"standard_name": str, "standard_version": str,
  "clause_ref": str, "title": str, "text": str, "obligation_level": str,
  "confidence": 0.0-1.0, "supporting_text": str}}. ONLY populate this when the
  document is a standard, regulation, law, or formal policy that states normative
  obligations (e.g. GDPR, ISO 27001, NIS2, an internal security policy). Each
  item is one clause/article/control: "clause_ref" is its locator (e.g.
  "Art. 32", "A.8.24", "5.1"), "text" is the obligation in the document's own
  words, and "obligation_level" is one of [{_bullet(OBLIGATION_LEVELS)}]
  (MANDATORY for "shall"/"must", RECOMMENDED for "should", OPTIONAL for "may").
  Leave this array empty for ordinary documents.
- "equations": array of {{"standard_name": str, "standard_version": str,
  "clause_ref": str, "symbol": str, "title": str, "expression": str,
  "variables": [{{"symbol": str, "description": str, "unit": str}}],
  "latex": str, "confidence": 0.0-1.0, "supporting_text": str}}. ONLY populate
  this when the document is a standard that states normative *formulas/equations*
  (e.g. an engineering design code, an actuarial/financial standard, a metrology
  spec). "symbol" is the computed result's symbol (e.g. "V_Rd_c"); "expression"
  is the right-hand side as a single Python expression using the standard's
  variable symbols and only arithmetic plus these functions:
  [{_bullet(tuple(sorted(ALLOWED_FUNCTIONS)))}] (use ** for powers, * for every
  multiplication). List every variable the expression uses in "variables" with
  its unit, and put the original notation in "latex". Leave this array empty for
  documents that contain no formulas.

Rules:
- Use the controlled vocabularies above. If document_type does not fit, use
  "Other".
- Every array may be empty. Prefer fewer, higher-confidence items.
- "supporting_text" must be a short quote copied from the document, or "".
- Return ONLY the JSON object, no markdown fences, no commentary.

Example of the expected shape (values are illustrative, do not copy them):
{json.dumps(_EXAMPLE, indent=2)}

Document metadata:
- filename: {filename}
- file_type: {file_type}
{"- note: text was truncated to the first %d characters" % max_input_chars if truncated else ""}

--- BEGIN DOCUMENT TEXT ---
{body}
--- END DOCUMENT TEXT ---
"""
    return SYSTEM_PROMPT, instructions


__all__ = ["build_classification_prompt", "SYSTEM_PROMPT"]
