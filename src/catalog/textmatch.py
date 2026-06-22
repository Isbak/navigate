"""Offline string-similarity primitives shared across layers.

These pure, dependency-free helpers turn two surface names into a similarity
score in ``[0.0, 1.0]``. They power entity resolution in the knowledge layer
(``knowledge/resolution.py``) and domain canonicalization in the semantic layer
(``semantic/domains.py``). Keeping them here avoids a layer depending on another
just to reuse a string comparison.

The blend is a token-set Jaccard, a character-trigram Dice coefficient, and a
containment boost for the "X" vs "X Model" case. Embedding similarity is
intentionally out of scope: this is a fully-offline stand-in that needs no model.
"""

from __future__ import annotations

import re

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# A small set of generic descriptor words that rarely change a name's identity
# ("Release Governance" vs "Release Governance Model"). They are only stripped to
# build the *matching key*; a displayed canonical name is always a real,
# unmodified mention.
_GENERIC_SUFFIXES = {
    "model",
    "framework",
    "process",
    "approach",
    "strategy",
    "initiative",
    "programme",
    "program",
    "system",
    "platform",
    "capability",
    "team",
}


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    This is the case+punctuation normalization technique; it is the key two
    names must share to be considered an exact (non-fuzzy) match.
    """

    lowered = (name or "").lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", no_punct).strip()


def _key_tokens(name: str) -> frozenset[str]:
    """Token set of a normalized name with generic descriptor words removed."""

    tokens = [t for t in normalize_name(name).split() if t not in _GENERIC_SUFFIXES]
    # If stripping suffixes emptied the name (e.g. just "Model"), keep originals.
    if not tokens:
        tokens = normalize_name(name).split()
    return frozenset(tokens)


def _trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def _dice(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def similarity(a: str, b: str) -> float:
    """Return a similarity score in ``[0.0, 1.0]`` for two names.

    Combines a token-set Jaccard, a character-trigram Dice coefficient, and a
    containment boost: when one name's significant tokens are a subset of the
    other's (the "X" vs "X Model" case) the score is lifted into auto-merge
    range. Identical normalized names score exactly ``1.0``.
    """

    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    ta, tb = _key_tokens(a), _key_tokens(b)
    union = ta | tb
    jaccard = len(ta & tb) / len(union) if union else 0.0
    dice = _dice(na, nb)
    base = 0.5 * jaccard + 0.5 * dice

    # Containment: significant tokens of one are wholly inside the other (the
    # "X" vs "X Model" case). Only boost when the *smaller* set has at least two
    # significant tokens, so a lone generic term ("Governance", "Data") is never
    # auto-merged into a specific multi-word name ("Release Governance",
    # "Data Platform") - that subset is real but the things are different.
    smaller = ta if len(ta) <= len(tb) else tb
    if ta and tb and len(smaller) >= 2 and (ta <= tb or tb <= ta):
        base = max(base, 0.88 + 0.12 * jaccard)

    return max(0.0, min(1.0, base))


__all__ = ["normalize_name", "similarity"]
