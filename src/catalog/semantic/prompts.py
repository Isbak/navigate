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

from .models import DOCUMENT_TYPES, ENTITY_TYPES, RELATIONSHIP_PREDICATES

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
