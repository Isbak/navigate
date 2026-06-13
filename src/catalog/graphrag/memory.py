"""Conversation memory - lightweight session state for follow-up questions.

The assistant is conversational: a question like "What risks are associated with
*that*?" only makes sense relative to the previous turn. Memory stores, per
turn, the question asked and the objects that were retrieved, and resolves a
pronoun referent ("that", "it", "those", ...) back to the most recent turn's
focus objects.

Resolution is deliberately conservative and *additive*: it does not rewrite the
user's words, it simply carries the previous turn's seed objects forward as
extra retrieval seeds, and records what the referent resolved to so the answer
can say so explicitly. No global state is kept; a memory instance is one
conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intent import FOLLOW_UP_REFERENTS


@dataclass
class Turn:
    """One question/answer exchange's retained state."""

    question: str
    object_ids: list[str] = field(default_factory=list)
    object_labels: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    """The outcome of resolving a (possibly follow-up) question."""

    carried_ids: list[str] = field(default_factory=list)
    carried_labels: list[str] = field(default_factory=list)

    @property
    def is_follow_up(self) -> bool:
        return bool(self.carried_ids)


class ConversationMemory:
    """In-memory history for a single GraphRAG conversation."""

    def __init__(self, max_turns: int = 20) -> None:
        self._turns: list[Turn] = []
        self._max_turns = max_turns

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)

    @property
    def last(self) -> Turn | None:
        return self._turns[-1] if self._turns else None

    def record(
        self, question: str, object_ids: list[str], object_labels: list[str]
    ) -> None:
        self._turns.append(
            Turn(
                question=question,
                object_ids=list(object_ids),
                object_labels=list(object_labels),
            )
        )
        if len(self._turns) > self._max_turns:
            self._turns = self._turns[-self._max_turns :]

    def resolve(self, question: str, *, has_focus: bool) -> Resolution:
        """Carry forward prior objects when a question references them.

        ``has_focus`` is True when the current question already names its own
        objects; in that case there is nothing to resolve. Otherwise, if the
        question contains a referent pronoun and a previous turn exists, that
        turn's objects are carried forward.
        """

        if has_focus or self.last is None:
            return Resolution()
        if not _contains_referent(question):
            return Resolution()
        return Resolution(
            carried_ids=list(self.last.object_ids),
            carried_labels=list(self.last.object_labels),
        )


def _contains_referent(question: str) -> bool:
    tokens = set(question.lower().replace("?", " ").replace(",", " ").split())
    return any(referent in tokens for referent in FOLLOW_UP_REFERENTS)


__all__ = ["Turn", "Resolution", "ConversationMemory"]
