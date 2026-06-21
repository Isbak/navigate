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
        {"title": "Adopt Launchpad operating model",
         "decision_text": "Adopt the Launchpad/Mission operating model",
         "confidence": 0.84, "supporting_text": "We will move to a Launchpad model"},
    ],
    "risks": [
        {"title": "Unclear release ownership",
         "risk_description": "Unclear ownership between teams", "confidence": 0.7,
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


def chunk_text(text: str, size: int, overlap: int = 0) -> list[str]:
    """Split ``text`` into chunks of ``size`` chars with ``overlap`` between them.

    A document that already fits in one chunk yields a single-element list, so
    callers behave exactly as before for short documents. ``overlap`` keeps an
    equation or sentence that straddles a boundary intact in at least one chunk.
    """

    body = text or ""
    if size <= 0 or len(body) <= size:
        return [body]
    step = max(1, size - max(0, overlap))
    return [body[start : start + size] for start in range(0, len(body), step)]


# The classification schema, controlled vocabularies, rules, and worked example
# never change between documents, so they live in the *system* prompt. Keeping
# this block constant lets the Claude provider cache it (prompt caching) and have
# the model reuse it across every chunk and document instead of re-reading ~1k
# tokens of instructions on every call. Only the per-document metadata and text
# go in the user prompt below.
_CLASSIFICATION_INSTRUCTIONS = f"""\
Analyze the document text the user provides and return a single JSON object with
these keys:

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
- "decisions": array of {{"title": str, "decision_text": str, "confidence":
  0.0-1.0, "supporting_text": str}}. Decisions the document appears to make.
  "title" is a short (<= 8 words) canonical name for the decision so the same
  decision phrased differently across documents collapses to one (e.g. "Adopt
  Launchpad operating model"); "decision_text" is the full decision. Do NOT mark
  anything as approved; these are candidates only.
- "risks": array of {{"title": str, "risk_description": str, "confidence":
  0.0-1.0, "supporting_text": str}}. "title" is a short (<= 8 words) canonical
  name for the risk (e.g. "Unclear release ownership"); "risk_description" is the
  full risk.
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
- For each entity choose the single most specific entity_type, and do NOT emit
  the same name under more than one type. Refer to a given thing by one
  consistent name throughout your output so it resolves to a single object.
- Every array may be empty. Prefer fewer, higher-confidence items.
- "supporting_text" must be a short quote copied from the document, or "".
- Return ONLY the JSON object, no markdown fences, no commentary.

Example of the expected shape (values are illustrative, do not copy them):
{json.dumps(_EXAMPLE, indent=2)}"""


# The full, constant system prompt: persona + schema/rules/example. Built once at
# import so every call reuses the exact same string (a requirement for caching).
CLASSIFICATION_SYSTEM = SYSTEM_PROMPT + "\n\n" + _CLASSIFICATION_INSTRUCTIONS


def build_classification_prompt(
    metadata: dict,
    text: str,
    *,
    max_input_chars: int = 12000,
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for one document (or chunk).

    The system prompt is the constant :data:`CLASSIFICATION_SYSTEM` (persona +
    schema + rules + example) - identical for every call so it can be cached. The
    user prompt carries only the per-document parts: ``metadata`` (filename,
    file_type, ...) and ``text``, truncated to ``max_input_chars`` as a safety
    net. When a document is processed in pieces, ``chunk_index`` and
    ``chunk_total`` (1-based count) are surfaced so the model knows it is seeing
    part of a longer document.
    """

    body = text or ""
    truncated = len(body) > max_input_chars
    if truncated:
        body = body[:max_input_chars]

    filename = metadata.get("filename", metadata.get("artifact_id", "unknown"))
    file_type = metadata.get("file_type", "unknown")
    chunk_note = (
        "- note: this is chunk %d of %d of a longer document; extract only what "
        "this chunk supports\n" % (chunk_index + 1, chunk_total)
        if chunk_total > 1
        else ""
    )
    truncation_note = (
        "- note: text was truncated to the first %d characters\n" % max_input_chars
        if truncated
        else ""
    )

    user = f"""\
Document metadata:
- filename: {filename}
- file_type: {file_type}
{chunk_note}{truncation_note}
--- BEGIN DOCUMENT TEXT ---
{body}
--- END DOCUMENT TEXT ---
"""
    return CLASSIFICATION_SYSTEM, user


__all__ = [
    "build_classification_prompt",
    "chunk_text",
    "SYSTEM_PROMPT",
    "CLASSIFICATION_SYSTEM",
]
