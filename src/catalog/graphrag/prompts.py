"""Prompt construction and hallucination controls.

These prompts are where the "knowledgeable analyst, not a guessing chatbot"
behaviour is enforced. The system prompt binds the model to the supplied context
and forbids inventing objects, relationships, or facts; the user prompt carries
the structured graph context and the question. A dedicated insufficient-evidence
sentinel lets every reasoning mode degrade to an honest "I don't know" rather
than a fabrication.

Nothing here calls a model - these are pure string builders, so they are trivial
to unit-test and identical across providers.
"""

from __future__ import annotations

# The exact phrase the assistant must emit when it cannot answer from the graph.
NO_EVIDENCE_RESPONSE = "No supporting evidence found."

SYSTEM_PROMPT = (
    "You are a precise knowledge-graph analyst. You answer ONLY from the "
    "structured graph context you are given: the listed Knowledge Objects, "
    "Relationships, and Evidence.\n\n"
    "Rules:\n"
    "1. Use only the supplied objects, relationships, and evidence. Do not use "
    "outside knowledge.\n"
    "2. Never invent knowledge objects, relationships, documents, or quotes.\n"
    "3. Support each claim by citing the evidence handles (e.g. [E1], [E2]) and "
    "the knowledge objects you relied on.\n"
    "4. Reason over the relationships to connect objects; explain the chain.\n"
    f"5. If the context lacks the evidence to answer, reply exactly: "
    f"\"{NO_EVIDENCE_RESPONSE}\" and nothing else.\n"
    "6. Be concise and factual. You are an analyst, not a chatbot."
)


def _instruction_for(reasoning_type: str) -> str:
    mode = str(reasoning_type)
    if mode == "path":
        return (
            "Explain how the two objects are connected by walking the "
            "relationship chain between them, step by step."
        )
    if mode == "impact":
        return (
            "Summarise what is affected by the focus object, grouping the "
            "impact by the type of connected object (capabilities, decisions, "
            "risks, teams, ...)."
        )
    if mode == "comparison":
        return (
            "Compare the two focus objects: their shared concepts, their unique "
            "connections, and any shared evidence or differences."
        )
    if mode == "evidence":
        return (
            "State precisely what evidence supports the conclusion, quoting the "
            "relevant documents."
        )
    if mode == "domain":
        return (
            "Give an analyst's overview of the focus area using the connected "
            "objects and their evidence."
        )
    return (
        "Answer the question directly using the related objects and their "
        "evidence."
    )


def build_answer_prompt(
    question: str,
    context_text: str,
    *,
    reasoning_type: str = "lookup",
    referent_note: str | None = None,
) -> str:
    """Assemble the user prompt: context, question, and a task instruction."""

    parts = ["GRAPH CONTEXT", "=============", context_text, ""]
    if referent_note:
        parts.append(f"NOTE: {referent_note}")
        parts.append("")
    parts.append(f"QUESTION: {question}")
    parts.append("")
    parts.append("TASK: " + _instruction_for(reasoning_type))
    parts.append(
        "Cite the evidence handles and knowledge objects you used. If the "
        f"context is insufficient, reply exactly \"{NO_EVIDENCE_RESPONSE}\"."
    )
    return "\n".join(parts)


__all__ = [
    "NO_EVIDENCE_RESPONSE",
    "SYSTEM_PROMPT",
    "build_answer_prompt",
]
