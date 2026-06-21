"""Entity resolution: turn many noisy mentions into a few canonical clusters.

This is the heart of consolidation. Given raw mentions like::

    "Release Governance"        (doc A)
    "Release Governance"        (doc B)
    "Release Governance Model"  (doc C)
    "Release governance"        (doc D)

it produces a single cluster whose canonical name is ``Release Governance``.

Techniques, in order of strength:

* **case + punctuation + whitespace normalization** - the cheap exact match that
  collapses "Release governance" and "Release Governance".
* **fuzzy matching** - a blend of token-set similarity and character-trigram Dice
  coefficient, with a containment boost so "Release Governance Model" merges into
  "Release Governance".
* **LLM-assisted merge suggestions** - optional. When a callable judge is
  supplied, borderline pairs (above the review threshold but below the auto-merge
  threshold) are confirmed or rejected by the model rather than guessed.

Merge confidence is recorded on every cluster. Pairs that are similar but below
the auto-merge threshold are *not* merged; they surface later as duplicate
candidates for a human to review. Embedding similarity is intentionally out of
scope for this phase (no vector-search UI), but the blend below is a pragmatic,
fully-offline stand-in that needs no model to run.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .models import Cluster, RawMention

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# A small set of generic descriptor words that rarely change an entity's
# identity ("Release Governance" vs "Release Governance Model"). They are only
# stripped to build the *matching key*; the displayed canonical name is always a
# real, unmodified mention.
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


@dataclass(frozen=True)
class ResolutionConfig:
    """Thresholds governing how aggressively mentions are merged.

    * ``auto_merge_threshold`` - at or above this similarity two names are merged
      automatically into one object.
    * ``review_threshold`` - between this and the auto-merge threshold a pair is a
      *duplicate candidate*: surfaced for human review, never auto-merged.
    * ``min_mention_confidence`` - mentions weaker than this are ignored entirely.
      Defaults to a non-zero floor so the long tail of low-confidence, one-off
      proposals does not each become its own knowledge object (pure noise).
    """

    auto_merge_threshold: float = 0.88
    review_threshold: float = 0.72
    min_mention_confidence: float = 0.3


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    This is the case+punctuation normalization technique; it is the key two
    mentions must share to be considered an exact (non-fuzzy) match.
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
    """Return a similarity score in ``[0.0, 1.0]`` for two entity names.

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


def _canonical_name(mentions: list[RawMention]) -> str:
    """Pick the representative display name for a cluster.

    Prefers the most frequently used surface form; ties break toward the most
    concise name (fewest words, then shortest), then alphabetically, so
    "Release Governance" wins over "Release Governance Model".
    """

    counts = Counter(m.name for m in mentions)
    best = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], len(kv[0].split()), len(kv[0]), kv[0].lower()),
    )
    return best[0][0]


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_mentions(
    mentions: list[RawMention],
    config: ResolutionConfig | None = None,
    *,
    merge_judge: Callable[[str, str, str], bool] | None = None,
) -> list[Cluster]:
    """Group ``mentions`` into canonical clusters, one per real-world thing.

    Mentions are first bucketed by ``(object_type, normalized_name)`` (exact
    match), then buckets of the same type are fuzzy-merged via union-find when
    their similarity reaches ``auto_merge_threshold``. ``merge_judge`` - if given
    - is consulted for borderline pairs (between the review and auto-merge
    thresholds) and may promote them to a merge; this is the LLM-assisted hook.

    The returned clusters carry a ``merge_confidence`` equal to the minimum
    pairwise similarity that held the cluster together (1.0 for exact groups).
    """

    config = config or ResolutionConfig()
    kept = [m for m in mentions if m.confidence >= config.min_mention_confidence]

    # Phase 2a: exact buckets keyed by (type, normalized name).
    buckets: dict[tuple[str, str], list[RawMention]] = {}
    for m in kept:
        buckets.setdefault((m.object_type, normalize_name(m.name)), []).append(m)

    bucket_keys = list(buckets)
    representatives = [_canonical_name(buckets[k]) for k in bucket_keys]
    uf = _UnionFind(len(bucket_keys))

    # Track, per merged pair, the similarity so we can report cohesion.
    edge_scores: dict[tuple[int, int], float] = {}

    # Phase 2b/3: fuzzy-merge buckets of the same object type.
    for i in range(len(bucket_keys)):
        type_i = bucket_keys[i][0]
        for j in range(i + 1, len(bucket_keys)):
            if bucket_keys[j][0] != type_i:
                continue
            score = similarity(representatives[i], representatives[j])
            merge = score >= config.auto_merge_threshold
            if (
                not merge
                and merge_judge is not None
                and score >= config.review_threshold
            ):
                # LLM-assisted merge suggestion for a borderline pair.
                merge = bool(merge_judge(representatives[i], representatives[j], type_i))
                if merge:
                    score = max(score, config.auto_merge_threshold)
            if merge:
                uf.union(i, j)
                edge_scores[(i, j)] = score

    # Assemble components into clusters.
    components: dict[int, list[int]] = {}
    for idx in range(len(bucket_keys)):
        components.setdefault(uf.find(idx), []).append(idx)

    clusters: list[Cluster] = []
    for members in components.values():
        merged_mentions: list[RawMention] = []
        for idx in members:
            merged_mentions.extend(buckets[bucket_keys[idx]])
        object_type = bucket_keys[members[0]][0]

        # Cohesion = the weakest similarity holding the component together.
        scores = [
            edge_scores[(a, b)]
            for a in members
            for b in members
            if (a, b) in edge_scores
        ]
        cohesion = min(scores) if scores else 1.0

        clusters.append(
            Cluster(
                object_type=object_type,
                canonical_name=_canonical_name(merged_mentions),
                mentions=merged_mentions,
                merge_confidence=round(cohesion, 3),
            )
        )

    # Stable, useful ordering: biggest, most-supported clusters first.
    clusters.sort(
        key=lambda c: (-len(c.artifact_ids), -len(c.mentions), c.canonical_name.lower())
    )
    return clusters


def duplicate_candidate_pairs(
    objects: list[tuple[str, str, str]], config: ResolutionConfig | None = None
) -> list[dict]:
    """Find object pairs similar enough to *maybe* be duplicates, but not merged.

    ``objects`` is ``[(id, object_type, canonical_name), ...]``. Returns pairs of
    the same type whose similarity sits in ``[review_threshold,
    auto_merge_threshold)`` - the band that was deliberately *not* auto-merged
    and should be reviewed by a human.
    """

    config = config or ResolutionConfig()
    out: list[dict] = []
    for i in range(len(objects)):
        id_i, type_i, name_i = objects[i]
        for j in range(i + 1, len(objects)):
            id_j, type_j, name_j = objects[j]
            if type_i != type_j:
                continue
            score = similarity(name_i, name_j)
            if config.review_threshold <= score < config.auto_merge_threshold:
                out.append(
                    {
                        "object_type": type_i,
                        "left_id": id_i,
                        "left_name": name_i,
                        "right_id": id_j,
                        "right_name": name_j,
                        "similarity": round(score, 3),
                    }
                )
    out.sort(key=lambda d: -d["similarity"])
    return out


def cross_type_duplicate_pairs(objects: list[tuple[str, str, str]]) -> list[dict]:
    """Find objects that share a name but differ in ``object_type``.

    ``objects`` is ``[(id, object_type, canonical_name), ...]``. Clustering only
    ever merges within a single ``object_type``, so the same real-world thing
    tagged ``Concept`` in one document and ``Capability`` in another becomes two
    separate objects. This surfaces those collisions (same normalized name,
    different type) for a human to reconcile - it never auto-merges them, because
    deciding the correct type is a genuine review call.
    """

    by_name: dict[str, list[tuple[str, str, str]]] = {}
    for oid, otype, name in objects:
        by_name.setdefault(normalize_name(name), []).append((oid, otype, name))

    out: list[dict] = []
    for group in by_name.values():
        if len({otype for _, otype, _ in group}) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if group[i][1] == group[j][1]:
                    continue
                out.append(
                    {
                        "left_id": group[i][0],
                        "left_type": group[i][1],
                        "left_name": group[i][2],
                        "right_id": group[j][0],
                        "right_type": group[j][1],
                        "right_name": group[j][2],
                    }
                )
    out.sort(key=lambda d: d["left_name"].lower())
    return out


__all__ = [
    "ResolutionConfig",
    "normalize_name",
    "similarity",
    "cluster_mentions",
    "duplicate_candidate_pairs",
    "cross_type_duplicate_pairs",
]
