"""Intent analysis - the first stage of the GraphRAG pipeline.

Before any retrieval happens, the question is parsed *deterministically* (no LLM)
into a small :class:`Intent`: the search objects named in the question, the
object type the user is asking about, the relationship they care about, and the
*reasoning type* that decides how retrieval and the prompt are shaped.

Keeping intent analysis rule-based makes it fast, free, and - crucially -
testable and reproducible: the same question always yields the same intent, so
the only non-deterministic step in the whole pipeline is the final LLM reasoning
over an already-fixed context.

Reasoning types (mirroring the prompt's taxonomy):

* ``LOOKUP``      - "What documents support Release Governance?"
* ``PATH``        - "What connects Launchpad Model to Release Management?"
* ``IMPACT``      - "What is the impact of Salesforce?"
* ``EVIDENCE``    - "What evidence supports this conclusion?"
* ``DOMAIN``      - "Give an overview of the Test & Release domain."
* ``COMPARISON``  - "Compare Release Governance and Platform Governance."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class ReasoningType(str, Enum):
    """How the assistant should reason over the retrieved graph."""

    LOOKUP = "lookup"
    PATH = "path"
    IMPACT = "impact"
    EVIDENCE = "evidence"
    DOMAIN = "domain"
    COMPARISON = "comparison"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


# A stored relationship predicate <- natural-language keywords that imply it.
# Order matters only for display; detection scans all and keeps the first hit.
_PREDICATE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("depends_on", ("depend", "rely on", "requires", "needs")),
    ("supports", ("support", "enable", "back ")),
    ("implements", ("implement", "deliver", "realize", "realise")),
    ("affects", ("affect", "impact")),
    ("owned_by", ("owned by", "ownership", "owns", "responsible for")),
    ("related_to", ("related", "relate", "associated", "connected with")),
    ("references", ("reference", "cites", "mentions")),
)

# Object type <- keyword stems. Stems are matched as substrings of the question.
_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Capability", ("capabilit",)),
    ("Decision", ("decision",)),
    ("Risk", ("risk",)),
    ("Team", ("team",)),
    ("Platform", ("platform",)),
    ("Technology", ("technolog",)),
    ("Initiative", ("initiative",)),
    ("Product", ("product",)),
    ("Process", ("process",)),
    ("Concept", ("concept",)),
)

# Phrases that say "show me the supporting evidence" rather than name an object.
_EVIDENCE_HINTS = (
    "evidence",
    "support this",
    "supports this",
    "this conclusion",
    "back this up",
    "how do you know",
    "what proves",
    "cite",
)

# Pronoun references that a follow-up question leaves for the memory layer.
FOLLOW_UP_REFERENTS = ("that", "those", "them", "it", "this", "these")


@dataclass(frozen=True)
class Intent:
    """The parsed intent of a question.

    ``focus_terms`` are the candidate object names found in the question (named
    entities or quoted phrases), in order of appearance; the retrieval layer
    resolves them to stable object ids. ``relationship_focus`` /
    ``object_type_focus`` narrow what neighbours matter, and ``evidence_focus``
    flags an explicit "show me the evidence" question.
    """

    question: str
    reasoning_type: ReasoningType
    focus_terms: list[str] = field(default_factory=list)
    relationship_focus: str | None = None
    object_type_focus: str | None = None
    evidence_focus: bool = False
    has_referent: bool = False

    @property
    def needs_two_objects(self) -> bool:
        """Path and comparison reasoning operate on a *pair* of objects."""

        return self.reasoning_type in (ReasoningType.PATH, ReasoningType.COMPARISON)


def _detect_reasoning(low: str) -> ReasoningType:
    if "compare" in low or "comparison" in low or "difference" in low or "differ" in low:
        return ReasoningType.COMPARISON
    if "impact" in low or "impacted" in low or "ripple" in low:
        return ReasoningType.IMPACT
    if (
        "connect" in low
        or "path" in low
        or "between" in low
        or "link " in low
        or "trace" in low
        or "how is" in low
        or "how are" in low
        or "how does" in low
    ):
        return ReasoningType.PATH
    if any(hint in low for hint in _EVIDENCE_HINTS):
        return ReasoningType.EVIDENCE
    if "overview" in low or "explore" in low or "tell me about the" in low or "domain" in low:
        return ReasoningType.DOMAIN
    return ReasoningType.LOOKUP


def _detect_predicate(low: str) -> str | None:
    for predicate, keywords in _PREDICATE_KEYWORDS:
        if any(keyword in low for keyword in keywords):
            return predicate
    return None


def _detect_object_type(low: str) -> str | None:
    for object_type, stems in _TYPE_KEYWORDS:
        if any(stem in low for stem in stems):
            return object_type
    return None


def _detect_evidence_focus(low: str) -> bool:
    return any(hint in low for hint in _EVIDENCE_HINTS)


def _quoted_phrases(question: str) -> list[str]:
    """Extract quoted spans ("...", '...') as explicit focus terms."""

    phrases: list[str] = []
    for quote in ('"', "'"):
        parts = question.split(quote)
        # Odd-indexed parts are the contents between matching quotes.
        for inside in parts[1::2]:
            inside = inside.strip()
            if inside:
                phrases.append(inside)
    return phrases


def extract_focus_terms(question: str, known_labels: Iterable[str]) -> list[str]:
    """Find the object names a question refers to, in order of appearance.

    Matches each known object label as a case-insensitive substring of the
    question, then drops any label wholly contained in a longer matched label
    (so "Release" disappears when "Release Governance" also matched). Quoted
    phrases are always kept, even if they are not exact labels, because the
    retrieval layer can still resolve them fuzzily.
    """

    low = question.lower()
    matched: list[tuple[int, str]] = []
    for label in known_labels:
        if not label:
            continue
        position = low.find(label.lower())
        if position >= 0:
            matched.append((position, label))

    lowered = [label.lower() for _, label in matched]
    kept: list[tuple[int, str]] = []
    for position, label in matched:
        this = label.lower()
        # Skip a label that is a proper substring of another matched label.
        if any(this != other and this in other for other in lowered):
            continue
        kept.append((position, label))

    for phrase in _quoted_phrases(question):
        if not any(phrase.lower() == label.lower() for _, label in kept):
            kept.append((low.find(phrase.lower()), phrase))

    kept.sort(key=lambda pair: (pair[0] if pair[0] >= 0 else len(low), pair[1]))

    ordered: list[str] = []
    seen: set[str] = set()
    for _, label in kept:
        key = label.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(label)
    return ordered


def _has_referent(low: str) -> bool:
    tokens = set(low.replace("?", " ").replace(",", " ").split())
    return any(referent in tokens for referent in FOLLOW_UP_REFERENTS)


def analyze_intent(question: str, known_labels: Iterable[str]) -> Intent:
    """Parse a natural-language question into a structured :class:`Intent`.

    ``known_labels`` are the labels of objects currently in the approved graph;
    they let the analyzer recognise which named entities the question mentions
    without any LLM call.
    """

    low = question.lower()
    return Intent(
        question=question.strip(),
        reasoning_type=_detect_reasoning(low),
        focus_terms=extract_focus_terms(question, known_labels),
        relationship_focus=_detect_predicate(low),
        object_type_focus=_detect_object_type(low),
        evidence_focus=_detect_evidence_focus(low),
        has_referent=_has_referent(low),
    )


__all__ = [
    "ReasoningType",
    "Intent",
    "analyze_intent",
    "extract_focus_terms",
    "FOLLOW_UP_REFERENTS",
]
