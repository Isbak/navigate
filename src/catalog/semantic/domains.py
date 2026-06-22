"""Domain canonicalization for the semantic layer.

The classifier discovers domains freely from each document, which lets a single
dense standard explode into a dozen near-duplicate domains ("Steel Design",
"Steel Construction", "Structural Engineering", ...). This module de-noises that
result deterministically, after classification and before persistence:

1. drop domains below a confidence floor,
2. map each surviving name onto a curated canonical name (exact/alias match, or
   a fuzzy match above ``map_threshold``),
3. fuzzy-merge whatever is left among itself above ``merge_threshold``,
4. dedupe by normalized name, keeping the strongest confidence per domain.

Configuration lives in ``config/domains.yml``. The loader is tolerant: a missing
file yields permissive defaults (floor only, no taxonomy, unlisted domains kept)
so the layer keeps discovering domains out of the box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..textmatch import normalize_name, similarity
from .models import DomainScore

DEFAULT_DOMAINS_CONFIG_PATH = Path("config/domains.yml")

DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_MERGE_THRESHOLD = 0.88
DEFAULT_MAP_THRESHOLD = 0.90


@dataclass(frozen=True)
class DomainTaxonomy:
    """Resolved domain-canonicalization settings.

    ``canonical`` is the list of preferred display names. ``aliases`` maps the
    *normalized* form of every known synonym (and of each canonical name itself)
    to its canonical display name, for O(1) exact lookup.
    """

    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD
    map_threshold: float = DEFAULT_MAP_THRESHOLD
    allow_unlisted: bool = True
    canonical: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)


def load_domain_taxonomy(
    path: str | Path = DEFAULT_DOMAINS_CONFIG_PATH,
) -> DomainTaxonomy:
    """Load ``config/domains.yml``; a missing file yields permissive defaults."""

    config_path = Path(path)
    if not config_path.exists():
        return DomainTaxonomy()

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    canonical: list[str] = []
    aliases: dict[str, str] = {}
    for entry in raw.get("canonical") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        canonical.append(name)
        # A canonical name is its own alias so exact lookup always resolves it.
        aliases[normalize_name(name)] = name
        for alias in entry.get("aliases") or []:
            key = normalize_name(str(alias))
            if key:
                aliases[key] = name

    return DomainTaxonomy(
        min_confidence=float(raw.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
        merge_threshold=float(raw.get("merge_threshold", DEFAULT_MERGE_THRESHOLD)),
        map_threshold=float(raw.get("map_threshold", DEFAULT_MAP_THRESHOLD)),
        allow_unlisted=bool(raw.get("allow_unlisted", True)),
        canonical=tuple(canonical),
        aliases=aliases,
    )


def _map_to_canonical(name: str, taxonomy: DomainTaxonomy) -> str | None:
    """Return the canonical name for ``name``, or ``None`` if it isn't in the taxonomy."""

    exact = taxonomy.aliases.get(normalize_name(name))
    if exact is not None:
        return exact
    best, best_score = None, taxonomy.map_threshold
    for canonical in taxonomy.canonical:
        score = similarity(name, canonical)
        if score >= best_score:
            best, best_score = canonical, score
    return best


def canonicalize_domains(domains: list[DomainScore], taxonomy: DomainTaxonomy) -> list[DomainScore]:
    """De-noise a document's discovered domains per ``taxonomy``.

    Drops sub-floor domains, maps known synonyms onto canonical names, fuzzy-merges
    the remaining near-duplicates, and keeps the highest confidence seen for each
    resulting domain. Output order follows first appearance, so the model's lead
    domain stays first.
    """

    # 1. Confidence floor.
    kept = [d for d in domains if d.confidence >= taxonomy.min_confidence]

    # 2. Map onto canonical names; drop unlisted ones only when not allowed.
    mapped: list[DomainScore] = []
    for d in kept:
        canonical = _map_to_canonical(d.domain, taxonomy)
        if canonical is not None:
            mapped.append(DomainScore(domain=canonical, confidence=d.confidence))
        elif taxonomy.allow_unlisted:
            mapped.append(d)

    # 3 + 4. Collapse into canonical buckets: an entry joins an existing bucket
    # when names normalize equal or are similar enough to auto-merge. The first
    # surface form seen wins the display name; confidence is the bucket max.
    out: list[DomainScore] = []
    for d in mapped:
        for i, existing in enumerate(out):
            if normalize_name(d.domain) == normalize_name(existing.domain) or (
                similarity(d.domain, existing.domain) >= taxonomy.merge_threshold
            ):
                if d.confidence > existing.confidence:
                    out[i] = DomainScore(domain=existing.domain, confidence=d.confidence)
                break
        else:
            out.append(d)
    return out


__all__ = [
    "DomainTaxonomy",
    "load_domain_taxonomy",
    "canonicalize_domains",
    "DEFAULT_DOMAINS_CONFIG_PATH",
]
