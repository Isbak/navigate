"""Prompt construction for *source code* classification.

This is the code counterpart of :mod:`catalog.semantic.prompts`. It returns the
**same JSON contract** so :func:`catalog.semantic.parser.parse_classification_response`
parses it unchanged - but the persona and instructions are tuned for reading a
source file: identify what the module does, the classes/functions/APIs it
defines, the libraries it depends on, and the risks (security, coupling) and
notable design decisions it embodies. Deterministic structure (imports, symbol
names, line spans) is added separately from the syntax tree; the model's job
here is the semantic layer the parser cannot infer.

``CODE_CLASSIFICATION_SYSTEM`` is constant and built once at import so the
Claude provider can cache it across every chunk and file (prompt caching),
exactly like ``CLASSIFICATION_SYSTEM``.
"""

from __future__ import annotations

from .models import ENTITY_TYPES, RELATIONSHIP_PREDICATES
from .prompts import _bullet

# Code-oriented subsets of the shared vocabularies, surfaced in the prompt so the
# model prefers them. Unknown values are still tolerated/normalized by the parser.
_CODE_ENTITY_TYPES = (
    "Module",
    "Class",
    "Function",
    "Library",
    "Service",
    "Interface",
    "API",
    "Technology",
    "Concept",
)
_CODE_PREDICATES = (
    "imports",
    "depends_on",
    "calls",
    "implements",
    "extends",
    "exposes",
    "defines",
    "references",
)

CODE_SYSTEM_PROMPT = (
    "You are a meticulous staff software engineer performing code review for a "
    "knowledge catalog. You read a single source file and propose structured "
    "knowledge about it. You never invent behaviour: every item you return "
    "includes a confidence score between 0.0 and 1.0 and, where possible, a "
    "short supporting snippet copied verbatim from the code. When unsure, lower "
    "the confidence rather than omitting the item. Respond with a single JSON "
    "object and nothing else."
)

_CODE_INSTRUCTIONS = f"""\
Analyze the source code the user provides and return a single JSON object with
these keys:

- "document_type": always "Source Code".
- "type_confidence": number 0.0-1.0.
- "short_summary": <= 100 words - what this file/module is responsible for.
- "long_summary": <= 500 words - how it works and how it fits the wider system.
- "domains": array of {{"domain": str, "confidence": 0.0-1.0}}. The functional
  area the code serves, e.g. "Authentication", "Billing", "Data Access", "API".
- "capabilities": array of {{"name": str, "confidence": 0.0-1.0,
  "supporting_text": str}}. Capabilities the code implements, e.g. "User
  Authentication", "PDF Extraction".
- "entities": array of {{"entity_type": str, "name": str, "confidence":
  0.0-1.0, "supporting_text": str}}. entity_type is one of
  [{_bullet(_CODE_ENTITY_TYPES)}]. Use "Module" for the file as a whole,
  "Class"/"Function" for the definitions it declares, "Library" for an external
  package it imports, "Service"/"API" for an external system or endpoint it
  talks to, and "Interface" for a protocol/abstract type. Prefer the single most
  specific type and refer to each thing by one consistent name.
- "decisions": array of {{"title": str, "decision_text": str, "confidence":
  0.0-1.0, "supporting_text": str}}. Notable design/architecture choices visible
  in the code (a chosen pattern, a framework, a concurrency or caching strategy).
  "title" is a short (<= 8 words) canonical name. These are candidates only.
- "risks": array of {{"title": str, "risk_description": str, "confidence":
  0.0-1.0, "supporting_text": str}}. Concrete code risks: security issues
  (injection, hard-coded secrets, unsafe deserialization), reliability gaps
  (unhandled errors), or maintainability concerns (tight coupling, dead code).
  "title" is a short (<= 8 words) canonical name.
- "relationships": array of {{"subject": str, "predicate": str, "object": str,
  "confidence": 0.0-1.0, "supporting_text": str}}. predicate is one of
  [{_bullet(_CODE_PREDICATES)}]. Capture how the named entities relate, e.g. a
  module "imports" a library, a class "implements" an interface, a function
  "calls" a service, a class "extends" another class.
- "requirements": leave as an empty array [].
- "equations": leave as an empty array [].

Rules:
- Use the controlled vocabularies above. Full entity vocabulary:
  [{_bullet(ENTITY_TYPES)}]; full predicate vocabulary:
  [{_bullet(RELATIONSHIP_PREDICATES)}].
- Name a thing consistently so it resolves to one object across the file.
- Every array may be empty. Prefer fewer, higher-confidence items.
- "supporting_text" must be a short snippet copied from the code, or "".
- Return ONLY the JSON object, no markdown fences, no commentary."""

# Constant system prompt (persona + schema/rules), built once for prompt caching.
CODE_CLASSIFICATION_SYSTEM = CODE_SYSTEM_PROMPT + "\n\n" + _CODE_INSTRUCTIONS


def build_code_classification_prompt(
    metadata: dict,
    code: str,
    *,
    max_input_chars: int = 12000,
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for one source file (or chunk).

    Signature-compatible with
    :func:`catalog.semantic.prompts.build_classification_prompt` so the service
    can swap prompt builders without special-casing the call site. The language
    is read from ``metadata['language']``.
    """

    body = code or ""
    truncated = len(body) > max_input_chars
    if truncated:
        body = body[:max_input_chars]

    filename = metadata.get("filename", metadata.get("artifact_id", "unknown"))
    language = metadata.get("language", "code")
    chunk_note = (
        f"- note: this is chunk {chunk_index + 1} of {chunk_total} of a longer "
        "file; extract only what this chunk supports\n"
        if chunk_total > 1
        else ""
    )
    truncation_note = (
        f"- note: code was truncated to the first {max_input_chars} characters\n"
        if truncated
        else ""
    )

    user = f"""\
Source file metadata:
- filename: {filename}
- language: {language}
{chunk_note}{truncation_note}
--- BEGIN SOURCE CODE ---
{body}
--- END SOURCE CODE ---
"""
    return CODE_CLASSIFICATION_SYSTEM, user


__all__ = [
    "build_code_classification_prompt",
    "CODE_SYSTEM_PROMPT",
    "CODE_CLASSIFICATION_SYSTEM",
]
