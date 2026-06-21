"""LLM-assisted merge suggestions.

When a provider is available, borderline name pairs - similar enough to suspect
they are the same thing, but below the auto-merge threshold - are put to the
model: "do these two names refer to the same X?" The model answers yes/no and
the resolver only merges on a clear yes.

This is the single LLM touch-point in consolidation, and it is entirely
optional: the resolver runs fully offline without it. The parser is defensive,
treating any non-affirmative or unparseable answer as "no" so a flaky model can
never cause an unwanted merge.
"""

from __future__ import annotations

import re

from ..semantic.providers.base import BaseLLMProvider

SYSTEM_PROMPT = (
    "You are a precise data-deduplication assistant. You decide whether two "
    "short names refer to the same real-world entity. You answer with a single "
    "word: YES or NO. You only say YES when you are confident they are the same "
    "thing; differing scope, product, or team means NO."
)

_YES_RE = re.compile(r"\byes\b", re.IGNORECASE)
_NO_RE = re.compile(r"\bno\b", re.IGNORECASE)


def build_merge_prompt(name_a: str, name_b: str, object_type: str) -> str:
    return (
        f"Both of the following are of type \"{object_type}\".\n"
        f"Name A: {name_a}\n"
        f"Name B: {name_b}\n\n"
        "Do Name A and Name B refer to the same thing? Answer YES or NO only."
    )


def parse_merge_answer(raw: str) -> bool:
    """Return True only for a clear affirmative; anything else is False."""

    text = (raw or "").strip()
    # Strip qwen-style reasoning blocks before looking for the verdict.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if _NO_RE.search(text) and not _YES_RE.search(text):
        return False
    return bool(_YES_RE.search(text))


def make_merge_judge(provider: BaseLLMProvider, usage_sink: list | None = None):
    """Adapt a provider into a ``(a, b, type) -> bool`` merge judge.

    Any provider error is swallowed into a conservative "no merge" so that an
    unavailable model degrades to the deterministic resolver rather than
    aborting consolidation. When ``usage_sink`` is given, each call's token usage
    is appended to it so the caller can price and persist the merge cost.
    """

    from ..semantic.providers.base import LLMError

    def judge(name_a: str, name_b: str, object_type: str) -> bool:
        prompt = build_merge_prompt(name_a, name_b, object_type)
        try:
            raw = provider.generate(prompt, system=SYSTEM_PROMPT)
        except LLMError:
            return False
        if usage_sink is not None:
            usage = getattr(provider, "last_usage", None)
            if usage is not None:
                usage_sink.append(usage)
        return parse_merge_answer(raw)

    return judge


__all__ = [
    "SYSTEM_PROMPT",
    "build_merge_prompt",
    "parse_merge_answer",
    "make_merge_judge",
]
