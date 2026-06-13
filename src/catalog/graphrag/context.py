"""Context builder - turn a graph retrieval into a compact, traceable prompt.

The context handed to the LLM is structured, deterministic, and small. It is the
*only* knowledge the model is allowed to use, so it is laid out so the model (and
a human reading ``--show-context``) can cite it precisely:

    KNOWLEDGE OBJECTS   [id] Label (Type, conf 0.94) - description
    RELATIONSHIPS       Label --predicate--> Label
    EVIDENCE            [E1] doc_123 (conf 0.92): "quote"

Every evidence quote is given a stable ``[E#]`` handle so the model can reference
exactly which document supports each claim. Ordering is fully deterministic
(seeds first, then by confidence and id), so the same retrieval always produces
byte-identical context - which keeps prompts cacheable and tests stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intent import Intent
from .retrieval import GraphRetrieval, RetrievedEvidence


@dataclass
class ContextBundle:
    """The rendered context plus the evidence handle map used for citations."""

    text: str
    evidence_handles: dict[str, RetrievedEvidence] = field(default_factory=dict)
    object_count: int = 0
    relationship_count: int = 0
    evidence_count: int = 0

    @property
    def size(self) -> int:
        return len(self.text)


def _fmt_conf(value: float) -> str:
    return f"{value:.2f}"


def build_context(
    retrieval: GraphRetrieval,
    intent: Intent | None = None,
    *,
    max_relationships: int = 60,
) -> ContextBundle:
    """Render a :class:`GraphRetrieval` into a structured context block."""

    lines: list[str] = []

    if intent is not None:
        focus = ", ".join(intent.focus_terms) if intent.focus_terms else "(none named)"
        lines.append(f"QUESTION INTENT: {intent.reasoning_type} | focus: {focus}")
        if intent.relationship_focus:
            lines.append(f"RELATIONSHIP FOCUS: {intent.relationship_focus}")
        if intent.object_type_focus:
            lines.append(f"OBJECT TYPE FOCUS: {intent.object_type_focus}")
        lines.append("")

    lines.append("KNOWLEDGE OBJECTS:")
    if retrieval.objects:
        for obj in retrieval.objects:
            marker = "*" if obj.is_seed else " "
            line = (
                f"{marker} [{obj.id}] {obj.label} "
                f"({obj.type}, conf {_fmt_conf(obj.confidence)})"
            )
            if obj.description:
                line += f" - {obj.description}"
            lines.append(line)
    else:
        lines.append("  (none matched)")
    lines.append("")

    lines.append("RELATIONSHIPS:")
    if retrieval.relationships:
        for rel in retrieval.relationships[:max_relationships]:
            lines.append(
                f"  {rel.source_label} --{rel.predicate}--> {rel.target_label}"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    handles: dict[str, RetrievedEvidence] = {}
    lines.append("EVIDENCE:")
    if retrieval.evidence:
        for index, item in enumerate(retrieval.evidence, start=1):
            handle = f"E{index}"
            handles[handle] = item
            quote = item.quote.strip() or "(no quote captured)"
            lines.append(
                f"  [{handle}] {item.artifact_id} (supports {item.object_label}, "
                f"conf {_fmt_conf(item.confidence)}): \"{quote}\""
            )
    else:
        lines.append("  (no supporting evidence retrieved)")

    if retrieval.unresolved_terms:
        lines.append("")
        lines.append(
            "UNRESOLVED TERMS (not found in the graph): "
            + ", ".join(retrieval.unresolved_terms)
        )

    return ContextBundle(
        text="\n".join(lines),
        evidence_handles=handles,
        object_count=len(retrieval.objects),
        relationship_count=len(retrieval.relationships),
        evidence_count=len(retrieval.evidence),
    )


__all__ = ["ContextBundle", "build_context"]
